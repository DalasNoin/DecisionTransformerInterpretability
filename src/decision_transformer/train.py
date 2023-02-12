import os
import torch as t
import torch.nn as nn
from einops import rearrange
from tqdm import tqdm
import wandb

from .model import DecisionTransformer
from .offline_dataset import TrajectoryLoader
from .trainer import Trainer


def train(
        dt: DecisionTransformer,
        trajectory_data_set: TrajectoryLoader,
        env,
        make_env,
        batch_size=128,
        batches=1000,
        lr=0.0001,
        weight_decay=0.0,
        device="cpu",
        track=False,
        test_frequency=10,
        test_batches=10,
        eval_frequency=10,
        eval_episodes=10,
        initial_rtg=1.0,
        prob_go_from_end=0.1,
        eval_max_time_steps=100):

    loss_fn = nn.CrossEntropyLoss()

    dt = dt.to(device)

    optimizer = t.optim.Adam(dt.parameters(), lr=lr, weight_decay=weight_decay)
    # trainer = Trainer(
    #     model = dt,
    #     optimizer = optimizer,
    #     batch_size=batch_size,
    #     max_len=max_len,
    #     get_batch = trajectory_data_set.get_batch,
    #     scheduler=None, # no scheduler for now
    #     track = track,
    #     mask_action=env.action_space.n,
    #     )

    pbar = tqdm(range(batches))
    for batch in pbar:

        dt.train()

        s, a, _, _, rtg, timesteps, _ = trajectory_data_set.get_batch(
            batch_size,
            max_len=dt.n_ctx // 3,
            prob_go_from_end=prob_go_from_end)

        s.to(device)
        a.to(device)
        rtg.to(device)
        timesteps.to(device)

        if dt.time_embedding_type == "linear":
            timesteps = timesteps.to(t.float32)

        a[a == -10] = env.action_space.n  # dummy action for padding

        optimizer.zero_grad()

        state_preds, action_preds, reward_preds = dt.forward(
            states=s,
            actions=a.to(t.int32).unsqueeze(-1),
            rtgs=rtg[:, :-1, :],
            timesteps=timesteps.unsqueeze(-1)
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
        # loss = trainer.train_step(step=batch)

        pbar.set_description(f"Training DT: {loss.item():.4f}")

        if track:
            wandb.log({"train/loss": loss.item()}, step=batch)
            tokens_seen = (batch + 1) * batch_size * (dt.n_ctx // 3)
            wandb.log({"metrics/tokens_seen": tokens_seen}, step=batch)

        # # at test frequency
        if batch % test_frequency == 0:
            test(
                dt=dt,
                trajectory_data_set=trajectory_data_set,
                env=env,
                batch_size=batch_size,
                batches=test_batches,
                device=device,
                track=track,
                batch_number=batch)

        eval_env_func = make_env(
            env_id=env.spec.id,
            seed=batch,
            idx=0,
            capture_video=True,
            max_steps=min(dt.max_timestep, eval_max_time_steps),
            run_name=f"dt_eval_videos_{batch}",
            fully_observed=False,
            flat_one_hot=(trajectory_data_set.observation_type == "one_hot"),
        )

        if batch % eval_frequency == 0:
            evaluate_dt_agent(
                env_id=env.spec.id,
                dt=dt,
                env_func=eval_env_func,
                trajectories=eval_episodes,
                track=track,
                batch_number=batch,
                initial_rtg=-1,
                device=device)

            evaluate_dt_agent(
                env_id=env.spec.id,
                dt=dt,
                env_func=eval_env_func,
                trajectories=eval_episodes,
                track=track,
                batch_number=batch,
                initial_rtg=0,
                device=device)

            evaluate_dt_agent(
                env_id=env.spec.id,
                dt=dt,
                env_func=eval_env_func,
                trajectories=eval_episodes,
                track=track,
                batch_number=batch,
                initial_rtg=initial_rtg,
                device=device)

    return dt


def test(
        dt: DecisionTransformer,
        trajectory_data_set: TrajectoryLoader,
        env,
        batch_size=128,
        batches=10,
        device="cpu",
        track=False,
        batch_number=0):

    dt.eval()

    loss_fn = nn.CrossEntropyLoss()

    loss = 0
    n_correct = 0
    n_actions = 0

    pbar = tqdm(range(batches), desc="Testing DT")
    for i in pbar:

        s, a, r, d, rtg, timesteps, mask = trajectory_data_set.get_batch(
            batch_size, max_len=dt.n_ctx // 3)

        s.to(device)
        a.to(device)
        r.to(device)
        d.to(device)
        rtg.to(device)
        timesteps.to(device)
        mask.to(device)

        if dt.time_embedding_type == "linear":
            timesteps = timesteps.to(t.float32)

        a[a == -10] = env.action_space.n  # dummy action for padding

        state_preds, action_preds, reward_preds = dt.forward(
            states=s,
            actions=a.to(t.int32).unsqueeze(-1),
            rtgs=rtg[:, :-1, :],
            timesteps=timesteps.unsqueeze(-1)
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

    mean_loss = loss.item() / batches

    if track:
        wandb.log({"test/loss": mean_loss}, step=batch_number)
        wandb.log({"test/accuracy": accuracy}, step=batch_number)

    return mean_loss, accuracy


def evaluate_dt_agent(
        env_id: str,
        dt: DecisionTransformer,
        env_func,
        trajectories=300,
        track=False,
        batch_number=0,
        initial_rtg=0.98,
        use_tqdm=True,
        device="cpu"):

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
            wandb.log({"media/video": wandb.Video(
                path_to_video,
                fps=4,
                format="mp4",
                caption=f"{env_id}, after {batch_number} batch, episode length {i}, reward {new_reward}, rtg {initial_rtg}"
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
            wandb.log({f"eval/{str(initial_rtg)}/" +
                       key: value}, step=batch_number)

    return statistics
