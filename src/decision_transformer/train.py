import os
from typing import List

import torch as t
import torch.nn as nn
from einops import rearrange
from torch.utils.data import DataLoader, random_split
from torch.utils.data.sampler import WeightedRandomSampler
from tqdm import tqdm

import wandb

from .model import DecisionTransformer
from .offline_dataset_new import TrajectoryDataset


def train(
        dt: DecisionTransformer,
        trajectory_data_set: TrajectoryDataset,
        env,
        make_env,
        batch_size=128,
        lr=0.0001,
        weight_decay=0.0,
        device="cpu",
        track=False,
        train_epochs=100,
        test_epochs=10,
        test_frequency=10,
        eval_frequency=10,
        eval_episodes=10,
        initial_rtg=[0.0, 1.0],
        eval_max_time_steps=100):

    loss_fn = nn.CrossEntropyLoss()
    dt = dt.to(device)
    optimizer = t.optim.Adam(dt.parameters(), lr=lr, weight_decay=weight_decay)

    train_dataset, test_dataset = random_split(
        trajectory_data_set, [0.90, 0.10])

    # Create the train DataLoader
    train_sampler = WeightedRandomSampler(
        weights=trajectory_data_set.sampling_probabilities[train_dataset.indices],
        num_samples=len(train_dataset),
        replacement=True,
    )
    train_dataloader = DataLoader(
        train_dataset, batch_size=batch_size, sampler=train_sampler)

    # Create the test DataLoader
    test_sampler = WeightedRandomSampler(
        weights=trajectory_data_set.sampling_probabilities[test_dataset.indices],
        num_samples=len(test_dataset),
        replacement=True,
    )
    test_dataloader = DataLoader(
        test_dataset, batch_size=batch_size, sampler=test_sampler)

    train_batches_per_epoch = len(train_dataloader)
    pbar = tqdm(range(train_epochs))
    for epoch in pbar:
        for batch, (s, a, r, d, rtg, ti, m) in (enumerate(train_dataloader)):
            total_batches = epoch * train_batches_per_epoch + batch

            dt.train()

            if dt.time_embedding_type == "linear":
                ti = ti.to(t.float32)

            a[a == -10] = env.action_space.n  # dummy action for padding

            optimizer.zero_grad()

            _, action_preds, _ = dt.forward(
                states=s,
                actions=a.unsqueeze(-1),
                rtgs=rtg[:, :-1],
                timesteps=ti.unsqueeze(-1)
            )

            action_preds = rearrange(action_preds, 'b t a -> (b t) a')
            a_exp = rearrange(a, 'b t -> (b t)').to(t.int64)

            # ignore dummy action
            loss = loss_fn(
                action_preds[a_exp != env.action_space.n],
                a_exp[a_exp != env.action_space.n]
            )

            loss.backward()
            optimizer.step()

            pbar.set_description(f"Training DT: {loss.item():.4f}")

            if track:
                wandb.log({"train/loss": loss.item()}, step=total_batches)
                tokens_seen = (total_batches + 1) * \
                    batch_size * (dt.n_ctx // 3)
                wandb.log({"metrics/tokens_seen": tokens_seen},
                          step=total_batches)

        # # at test frequency
        if epoch % test_frequency == 0:
            test(
                dt=dt,
                dataloader=test_dataloader,
                env=env,
                epochs=test_epochs,
                track=track,
                batch_number=total_batches)

        eval_env_func = make_env(
            env_id=env.spec.id,
            seed=batch,
            idx=0,
            capture_video=True,
            max_steps=min(dt.max_timestep, eval_max_time_steps),
            run_name=f"dt_eval_videos_{batch}",
            fully_observed=False,
            flat_one_hot=(
                trajectory_data_set.observation_type == "one_hot"),
            # defensive coding, fix later.
            agent_view_size=env.observation_space['image'].shape[0] if "image" in list(
                env.observation_space.keys()) else 7,
        )

        if epoch % eval_frequency == 0:
            for rtg in initial_rtg:
                evaluate_dt_agent(
                    env_id=env.spec.id,
                    dt=dt,
                    env_func=eval_env_func,
                    trajectories=eval_episodes,
                    track=track,
                    batch_number=total_batches,
                    initial_rtg=float(rtg),
                    device=device)

    return dt


def test(
        dt: DecisionTransformer,
        dataloader: DataLoader,
        env,
        epochs=10,
        track=False,
        batch_number=0):

    dt.eval()

    loss_fn = nn.CrossEntropyLoss()

    loss = 0
    n_correct = 0
    n_actions = 0

    pbar = tqdm(range(epochs))
    test_batches_per_epoch = len(dataloader)

    for epoch in pbar:
        for batch, (s, a, r, d, rtg, ti, m) in (enumerate(dataloader)):
            if dt.time_embedding_type == "linear":
                ti = ti.to(t.float32)

            a[a == -10] = env.action_space.n

            _, action_preds, _ = dt.forward(
                states=s,
                actions=a.unsqueeze(-1),
                rtgs=rtg[:, :-1],
                timesteps=ti.unsqueeze(-1)
            )

            action_preds = rearrange(action_preds, 'b t a -> (b t) a')
            a_exp = rearrange(a, 'b t -> (b t)').to(t.int64)

            a_hat = t.argmax(action_preds, dim=-1)
            a_exp = rearrange(a, 'b t -> (b t)').to(t.int64)

            action_preds = action_preds[a_exp != env.action_space.n]
            a_hat = a_hat[a_exp != env.action_space.n]
            a_exp = a_exp[a_exp != env.action_space.n]

            n_actions += a_exp.shape[0]
            n_correct += (a_hat == a_exp).sum()
            loss += loss_fn(action_preds, a_exp)

            accuracy = n_correct.item() / n_actions
            pbar.set_description(f"Testing DT: Accuracy so far {accuracy:.4f}")

    mean_loss = loss.item() / epochs*test_batches_per_epoch

    if track:
        wandb.log({"test/loss": mean_loss}, step=batch_number)
        wandb.log({"test/accuracy": accuracy}, step=batch_number)

    return mean_loss, accuracy


def evaluate_dt_agent(
        env_ids: List[str],
        dt: DecisionTransformer,
        env_func,
        trajectories=300,
        track=False,
        batch_number=0,
        initial_rtg=0.98,
        use_tqdm=True,
        device="cpu"):

    if isinstance(env_ids, str):
        env_ids = [env_ids]

    dt.eval()

    env = env_func()
    video_path = os.path.join("videos", env.run_name)

    assert dt.n_ctx % 3 == 0, "n_ctx must be divisible by 3"
    max_len = dt.n_ctx // 3

    traj_lengths = []
    rewards = []
    n_terminated = 0
    n_truncated = 0
    reward_total = 0
    n_positive = 0

    if not os.path.exists(video_path):
        os.makedirs(video_path)

    videos = [i for i in os.listdir(video_path) if i.endswith(".mp4")]
    for video in videos:
        os.remove(os.path.join(video_path, video))
    videos = [i for i in os.listdir(video_path) if i.endswith(".mp4")]

    if use_tqdm:
        pbar = tqdm(range(trajectories), desc="Evaluating DT")
    else:
        pbar = range(trajectories)

    for seed in pbar:
        obs, _ = env.reset(seed=seed)
        obs = t.tensor(obs['image']).unsqueeze(0).unsqueeze(0)
        rtg = t.tensor([initial_rtg]).unsqueeze(0).unsqueeze(0)
        a = t.tensor([0]).unsqueeze(0).unsqueeze(0)
        timesteps = t.tensor([0]).unsqueeze(0).unsqueeze(0)

        obs = obs.to(device)
        rtg = rtg.to(device)
        a = a.to(device)
        timesteps = timesteps.to(device)

        if dt.time_embedding_type == "linear":
            timesteps = timesteps.to(t.float32)

        # get first action
        state_preds, action_preds, reward_preds = dt.forward(
            states=obs, actions=a, rtgs=rtg, timesteps=timesteps)

        new_action = t.argmax(action_preds, dim=-1)[0].item()
        new_obs, new_reward, terminated, truncated, info = env.step(new_action)

        i = 0
        while not (terminated or truncated):

            # concat init obs to new obs
            obs = t.cat(
                [obs, t.tensor(new_obs['image']).unsqueeze(0).unsqueeze(0).to(device)], dim=1)

            # add new reward to init reward
            rtg = t.cat([rtg, t.tensor(
                [rtg[-1][-1].item() - new_reward]).unsqueeze(0).unsqueeze(0).to(device)], dim=1)

            # add new timesteps
            timesteps = t.cat([timesteps, t.tensor(
                [timesteps[-1][-1].item()+1]).unsqueeze(0).unsqueeze(0).to(device)], dim=1)

            if dt.time_embedding_type == "linear":
                timesteps = timesteps.to(t.float32)

            a = t.cat(
                [a, t.tensor([new_action]).unsqueeze(0).unsqueeze(0).to(device)], dim=1)

            state_preds, action_preds, reward_preds = dt.forward(
                states=obs[:, -max_len:] if obs.shape[1] > max_len else obs,
                actions=a[:, -max_len:] if a.shape[1] > max_len else a,
                rtgs=rtg[:, -max_len:] if rtg.shape[1] > max_len else rtg,
                timesteps=timesteps[:, -
                                    max_len:] if timesteps.shape[1] > max_len else timesteps
            )
            action = t.argmax(action_preds, dim=-1)[0][-1].item()
            new_obs, new_reward, terminated, truncated, info = env.step(action)

            # print(f"took action  {action} at timestep {i} for reward {new_reward}")
            i = i + 1

            if use_tqdm:
                pbar.set_description(
                    f"Evaluating DT: Episode {seed} at timestep {i} for reward {new_reward}")

        traj_lengths.append(i)
        rewards.append(new_reward)

        n_positive = n_positive + (new_reward > 0)
        reward_total = reward_total + new_reward
        n_terminated = n_terminated + terminated
        n_truncated = n_truncated + truncated

        current_videos = [i for i in os.listdir(
            video_path) if i.endswith(".mp4")]
        if track and (len(current_videos) > len(videos)):  # we have a new video
            new_videos = [i for i in current_videos if i not in videos]
            assert len(new_videos) == 1, "more than one new video found, new videos: {}".format(
                new_videos)
            path_to_video = os.path.join(video_path, new_videos[0])
            wandb.log({f"media/video/{initial_rtg}/": wandb.Video(
                path_to_video,
                fps=4,
                format="mp4",
                caption=f"{env_ids[0]}, after {batch_number} batch, episode length {i}, reward {new_reward}, rtg {initial_rtg}"
            )}, step=batch_number)
        videos = current_videos  # update videos

    statistics = {
        "initial_rtg": initial_rtg,
        "prop_completed": n_terminated / trajectories,
        "prop_truncated": n_truncated / trajectories,
        "mean_reward": reward_total / trajectories,
        "prop_positive_reward": n_positive / trajectories,
        "mean_traj_length": sum(traj_lengths) / trajectories,
        "traj_lengths": traj_lengths,
        "rewards": rewards
    }

    env.close()
    if track:
        # log statistics at batch number but prefix with eval
        for key, value in statistics.items():
            if key == "initial_rtg":
                continue
            if key == "traj_lengths":
                wandb.log({f"eval/{str(initial_rtg)}/traj_lengths": wandb.Histogram(
                    value)}, step=batch_number)
            elif key == "rewards":
                wandb.log({f"eval/{str(initial_rtg)}/rewards": wandb.Histogram(
                    value)}, step=batch_number)
            wandb.log({f"eval/{str(initial_rtg)}/" +
                       key: value}, step=batch_number)

    return statistics
