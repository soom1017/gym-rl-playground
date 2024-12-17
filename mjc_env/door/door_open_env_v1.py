import os
import re
from pathlib import Path
import numpy as np
from gymnasium import utils
from gymnasium.envs.mujoco import MujocoEnv
from gymnasium.spaces import Box
import mujoco
from scipy.spatial.transform import Rotation as R

"""
강화학습의 출력이 End Effector의 pose인 Inverse Dynamics 환경
"""
GEOM = 5

cur_dir = Path(os.path.dirname(__file__))

class DoorOpenEnv(MujocoEnv, utils.EzPickle):
    metadata = {
        "render_modes": ["human", "rgb_array", "depth_array"],
        "render_fps": 100,
    }

    def __init__(self, episode_len=500, **kwargs):
        utils.EzPickle.__init__(self, **kwargs)

        observation_space = Box(low=-np.inf, high=np.inf, shape=(6,), dtype=np.float32)
        self.idv_action_space = Box(low=-np.inf, high=np.inf, shape=(7,), dtype=np.float32) # end effector pose 
                                                                                            # (x, y, z, qw, qx, qy, qz)

        MujocoEnv.__init__(
            self, 
            os.path.abspath(cur_dir / "scene2" / "mobile_fr3.xml"), 
            10, 
            observation_space=observation_space, 
            **kwargs
        )
        self.step_number = 0
        self.episode_len = episode_len
        # 충돌 판정에서 제외할 geom id: door_handle, finger 관련된 ids
        self.excluded_geom_ids = self._find_geom_ids()
        # Franka Reach Pose 반영
        self.init_qpos = self.data.qpos.ravel().copy()
        # self.init_qpos[3:10] += np.array([0.3295, -0.0929, -0.3062, -2.4366, 1.4139, 2.7500, 0.6700])
        self.init_qpos[3:10] += np.array([0, -0.7854, 0, -2.3562, 0, 1.5708, 0.7854])
        
        self.success_duration = 0
        print("Initialized DoorOpenEnv")
        
    def _set_action_space(self):
        self.action_space = self.idv_action_space
        return self.action_space

    def step(self, a):
        """
        a: desired pose of the end effector
        """
        self.data.mocap_pos[0] += a[:3] / 100
        mujoco.mj_step(self.model, self.data, self.frame_skip)
        self.step_number += 1

        obs = self._get_obs()
        rew, term = self._get_rew_done(obs)
        trunc = self.step_number >= self.episode_len
        
        return obs, rew, term, trunc, {}
    
    def reset_model(self):
        self.step_number = 0
        
        qpos = self.init_qpos + self.np_random.uniform(
            size=self.model.nq, low=-0.01, high=0.01
        )
        qvel = self.init_qvel + self.np_random.uniform(
            size=self.model.nv, low=-0.01, high=0.01
        )
        self.set_state(qpos, qvel)
        
        return self._get_obs()

    def _get_obs(self):
        obs = np.concatenate([
                            self.data.body("hand").xpos,     # (3,)
                            self.data.body("latch").xpos,    # (3,)
                        ])
        return obs
    
    def _get_rew_done(self, obs):
        # EE와 목표 지점 사이의 거리
        dist = np.linalg.norm(self.data.body("hand").xpos - self.data.body("latch").xpos)
        rew_dist = (1 / (1 + dist))
        # 관절 속도 패널티
        pen_qvel = -abs(self.data.qvel[3:10]).sum()
        # 충돌 패널티
        is_collided = self._process_collision()
        pen_collision = -1 if is_collided else 0
        
        success = dist < 0.1
        
        rew = success * 10 + rew_dist - pen_qvel * 0.001 - pen_collision * 10
        
        done = (self.success_duration > 20) | is_collided
        if success:
            self.success_duration += 1
        else:
            self.success_duration = 0
        
        return rew, done
    
    def _process_collision(self):
        # 충돌 판정
        for contact in self.data.contact:
            if contact.geom1 and contact.geom2 and (contact.geom1 not in self.excluded_geom_ids) and (contact.geom2 not in self.excluded_geom_ids):
                geom1_name = mujoco.mj_id2name(self.model, GEOM, contact.geom1)
                geom2_name = mujoco.mj_id2name(self.model, GEOM, contact.geom2)
                # print(f"Collision between {geom1_name} and {geom2_name} detected!")
                return True
        return False
            
        
    def _find_geom_ids(self):
        geom_ids = []
        # 문 손잡이는 잡아야 하므로 충돌 감지에서 제외
        handle_id = mujoco.mj_name2id(self.model, GEOM, "door_handle")
        geom_ids.append(handle_id)
        # 손가락은 문을 열기 위해 사용되므로 충돌 감지에서 제외
        for geom_id in range(self.model.ngeom):
            geom_name = mujoco.mj_id2name(self.model, GEOM, geom_id)
            if geom_name and re.match("^finger", geom_name):
                geom_ids.append(geom_id)
                
        return geom_ids