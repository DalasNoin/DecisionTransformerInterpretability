import pytest
import os
import torch as t
from dataclasses import dataclass
import numpy as np
import pickle
import torch
from src.utils import TrajectoryWriter
from src.decision_transformer.utils import load_decision_transformer
from src.environments import make_env


def test_trajectory_writer_numpy():

    @dataclass
    class DummyArgs:
        path = "tmp/test_trajectory_writer_output.pkl"

    args = DummyArgs()

    trajectory_writer = TrajectoryWriter(
        "tmp/test_trajectory_writer_writer.pkl", args)

    # test accumulate trajectory when all the objects are initialized as np arrays

    trajectory_writer.accumulate_trajectory(
        next_obs=np.array([1, 2, 3]),
        reward=np.array([1, 2, 3]),
        done=np.array([1, 0, 0]),
        truncated=np.array([1, 0, 0]),
        action=np.array([1, 2, 3]),
        info={"a": 1, "b": 2, "c": 3},
    )

    trajectory_writer.write()

    # get the size of the file in bytes
    assert os.path.getsize("tmp/test_trajectory_writer_writer.pkl") > 0
    # make sure it's less than 200 bytes
    assert os.path.getsize("tmp/test_trajectory_writer_writer.pkl") < 700

    with open("tmp/test_trajectory_writer_writer.pkl", "rb") as f:
        data = pickle.load(f)

        obs = data["data"]["observations"]
        assert type(obs) == np.ndarray
        assert obs.dtype == np.float64

        assert obs[0][0] == 1
        assert obs[0][1] == 2
        assert obs[0][2] == 3

        rewards = data["data"]["rewards"]
        assert type(rewards) == np.ndarray
        assert rewards.dtype == np.float64

        assert rewards[0][0] == 1
        assert rewards[0][1] == 2
        assert rewards[0][2] == 3

        dones = data["data"]["dones"]
        assert type(dones) == np.ndarray
        assert dones.dtype == bool

        assert dones[0][0]
        assert dones[0][1] == False
        assert dones[0][2] == False

        actions = data["data"]["actions"]
        assert type(actions) == np.ndarray
        assert actions.dtype == np.int64

        assert actions[0][0] == 1
        assert actions[0][1] == 2
        assert actions[0][2] == 3

        infos = data["data"]["infos"]
        assert type(infos) == np.ndarray
        assert infos.dtype == np.object

        assert infos[0]["a"] == 1
        assert infos[0]["b"] == 2
        assert infos[0]["c"] == 3


def test_trajectory_writer_torch():

    @dataclass
    class DummyArgs:
        pass

    args = DummyArgs()

    trajectory_writer = TrajectoryWriter(
        "tmp/test_trajectory_writer_writer.pkl", args)

    # test accumulate trajectory when all the objects are initialized as pytorch tensors

    # assert raises type error
    with pytest.raises(TypeError):
        trajectory_writer.accumulate_trajectory(
            next_obs=torch.tensor([1, 2, 3], dtype=torch.float64),
            reward=torch.tensor([1, 2, 3], dtype=torch.float64),
            done=torch.tensor([1, 0, 0], dtype=torch.bool),
            action=torch.tensor([1, 2, 3], dtype=torch.int64),
            info=[{"a": 1, "b": 2, "c": 3}],
        )


def test_trajectory_writer_lzma():

    @dataclass
    class DummyArgs:
        path = "tmp/test_trajectory_writer_output.xz"

    args = DummyArgs()

    trajectory_writer = TrajectoryWriter(
        "tmp/test_trajectory_writer_writer.xz", args)

    # test accumulate trajectory when all the objects are initialized as np arrays

    trajectory_writer.accumulate_trajectory(
        next_obs=np.array([1, 2, 3]),
        reward=np.array([1, 2, 3]),
        done=np.array([1, 0, 0]),
        truncated=np.array([1, 0, 0]),
        action=np.array([1, 2, 3]),
        info={"a": 1, "b": 2, "c": 3},
    )

    trajectory_writer.write()

    # get the size of the file in bytes
    assert os.path.getsize("tmp/test_trajectory_writer_writer.xz") > 0
    # make sure it's less than 200 bytes
    assert os.path.getsize("tmp/test_trajectory_writer_writer.xz") < 400


def test_load_decision_transformer():

    model_path = "models/MiniGrid-Dynamic-Obstacles-8x8-v0/demo_model_overnight_training.pt"
    env = make_env(env_ids="MiniGrid-Dynamic-Obstacles-8x8-v0",
                   seed=0, idx=0, capture_video=False, run_name="test")()
    model = load_decision_transformer(model_path, env)

    assert model.env == env
    assert model.env.action_space.n == 3
    assert model.n_ctx == 3
    assert model.d_model == 128
    assert model.n_heads == 2
    assert model.n_layers == 1
    assert model.normalization_type is None


def test_load_decision_transformer_linear_time():

    model_path = "models/linear_model_not_performant.pt"
    env = make_env(env_ids="MiniGrid-Dynamic-Obstacles-8x8-v0",
                   seed=0, idx=0, capture_video=False, run_name="test")()
    model = load_decision_transformer(model_path, env)

    assert model.env == env
    assert model.env.action_space.n == 3
    assert model.n_ctx == 3
    assert model.d_model == 128
    assert model.n_heads == 2
    assert model.n_layers == 1
    assert model.normalization_type is None


def test_load_decision_key_door():

    model_path = "models/MiniGrid-DoorKey-8x8-v0/first_pass.pt"
    env = make_env(env_ids="MiniGrid-DoorKey-8x8-v0",
                   seed=0, idx=0, capture_video=False, run_name="test")()
    model = load_decision_transformer(model_path, env)

    assert model.env == env
    assert model.env.action_space.n == 7
    assert model.n_ctx == 3
    assert model.d_model == 128
    assert model.n_heads == 2
    assert model.n_layers == 1
    assert model.normalization_type is None
