from typing import List
import numpy as np
import gymnasium as gym
from minigrid.wrappers import OneHotPartialObsWrapper, FullyObsWrapper, ObservationWrapper
from gymnasium import spaces


def make_env(
    env_ids: List[str],
    env_prob: List[float] = None,
    seed: int = 1,
    idx: int = 0,
    capture_video: bool = False,
    run_name: str = "test",
    render_mode="rgb_array",
    max_steps=100,
    fully_observed=False,
    flat_one_hot=False,
    agent_view_size=7,
    video_frequency=50
):
    """Return a function that returns an environment after setting up boilerplate.

    Example use:

    >>>  envs = gym.vector.SyncVectorEnv(
            [make_env(
                env_ids = args.env_id,
                env_prob = args.env_prob,
                seed = args.seed + i,
                idx = i,
                capture_video = args.capture_video,
                run_name = run_name,
                max_steps=args.max_steps,
                fully_observed=args.fully_observed,
                flat_one_hot=args.one_hot_obs,
                agent_view_size=args.view_size,
                render_mode = render_mode
                ) for i in range(args.num_envs)]
        )
    """

    if isinstance(env_ids, str):
        env_ids = [env_ids]

    if env_prob is None:
        env_prob = [1 / len(env_ids)] * len(env_ids)

    # only one of fully observed or flat one hot can be true.
    assert not (
        fully_observed and flat_one_hot), "Can't have both fully_observed and flat_one_hot."

    def thunk():

        kwargs = {}
        if render_mode:
            kwargs["render_mode"] = render_mode
        if max_steps:
            kwargs["max_steps"] = max_steps

        env_id = np.random.choice(env_ids, p=env_prob)
        env = gym.make(env_id, **kwargs)

        env = gym.wrappers.RecordEpisodeStatistics(env)
        if capture_video:
            if idx == 0:
                env = gym.wrappers.RecordVideo(
                    env,
                    f"videos/{run_name}",
                    # Video every 50 runs for env #1
                    episode_trigger=lambda x: x % video_frequency == 0,
                    disable_logger=True
                )

        # hard code for now!
        if env_id.startswith("MiniGrid"):
            if fully_observed:
                env = FullyObsWrapper(env)
            if agent_view_size != 7:
                env = ViewSizeWrapper(env, agent_view_size=agent_view_size)
            if flat_one_hot:
                env = OneHotPartialObsWrapper(env)

        obs = env.reset(seed=seed)
        env.action_space.seed(seed)
        env.observation_space.seed(seed)
        env.run_name = run_name
        return env

    return thunk


class ViewSizeWrapper(ObservationWrapper):
    """
    Wrapper to customize the agent field of view size.
    This cannot be used with fully observable wrappers.

    Example:
        >>> import miniworld
        >>> import gymnasium as gym
        >>> import matplotlib.pyplot as plt
        >>> from minigrid.wrappers import ViewSizeWrapper
        >>> env = gym.make("MiniGrid-LavaCrossingS11N5-v0")
        >>> obs, _ = env.reset()
        >>> obs['image'].shape
        (7, 7, 3)
        >>> env_obs = ViewSizeWrapper(env, agent_view_size=5)
        >>> obs, _ = env_obs.reset()
        >>> obs['image'].shape
        (5, 5, 3)
    """

    def __init__(self, env, agent_view_size=7):
        super().__init__(env)

        assert agent_view_size % 2 == 1
        assert agent_view_size >= 3

        self.agent_view_size = agent_view_size

        # Compute observation space with specified view size
        new_image_space = gym.spaces.Box(
            low=0, high=255, shape=(agent_view_size, agent_view_size, 3), dtype="uint8"
        )

        # Override the environment's observation spaceexit
        self.observation_space = spaces.Dict(
            {**self.observation_space.spaces, "image": new_image_space}
        )

    def observation(self, obs):
        env = self.unwrapped

        grid, vis_mask = env.gen_obs_grid(self.agent_view_size)

        # Encode the partially observable view into a numpy array
        image = grid.encode(vis_mask)

        return {**obs, "image": image}
