import verifiers as vf
from verifiers import Messages, State

# on every rollout, is a new env created? or is a single one created - I think that a single env is used and then each time setup_state is called?
# in that case I should probably have a single sim objcet tied to the environment and then each state only has the simulator id attached

# things to figure out
"""
1. does the env get recreated each time for every rollout or group of rollouts?
"""

"""
FOR NOW KEEP SAME STARTING POINT FOR TASKS!!!!!! DO THIS FOR SIMPLICITY
"""

"""
Sim pattern: create master sim -> each rollout, clone and on task completion, close the master sim
"""


class IPhoneCua(vf.MultiTurnEnv):
    def __init__(self, max_turns: int = 15, **kwargs):
        super().__init__(max_turns, kwargs)
        self.sim = None

    async def env_response(
        self, messages: Messages, state: State, **kwargs
    ) -> tuple[Messages, State]:
        sim_state = state.get("sim_state", {})
        last_msg = messages[-1]
        if last_msg["role"] != "assistant":
            return [], state

        tool_use = last_msg["tool_use"]
        if tool_use is None:
            sim_state.update({"terminated": True, "error": "No tool use.", "score": 0})
            return messages, state
        else:
            if tool_use == "FINISHED":
                sim_state.update(
                    {
                        "terminated": True,
                        "error": None,
                        "score": judge(sim_state.last_obs),
                    }
                )
                return messages, state
            else:
                env_res = await self.sim.step(sim_state["id"], tool_use=tool_use)
                error = env_res["error"]
                obs = env_res["obs"]

                sim_state.update(
                    {
                        "terminated": error is not None,
                        "error": error,
                        "last_obs": obs,
                        "score": 0,
                    }
                )

                return messages + [obs], state if error is not None else messages, state

    async def is_completed(self, messages: Messages, state: State, **kwargs) -> bool:
        sim_state = state.get("sim_state", {})
        terminated = sim_state.get("terminated", False)
        timeout = sim_state["turn"] >= self.max_turns > 0
        if timeout:
            sim_state.update({"status": "timeout"})
        completed = terminated or timeout
        if completed:
            self.sim.cleanup(sim_state["id"])
        return completed

    async def setup_state(self, state: State, **kwargs) -> State:
        sim_state = state.setdefault("sim", {})
        sim_state.update(
            {
                "step": 0,
                "status": "in_progress",
                "terminated": False,
                "error": None,
                "sim": None,
                "score": 0,
            }
        )

        if self.sim == None:
            self.sim = await self._setup_sim_vm()
        sim_id = self.sim.new()
        sim_state["id"] = sim_id
        obs = await self.sim.reset_sim(sim_id)
        sim_state["latest_obs"] = obs
        return state


def load_environment(**kwargs) -> vf.Environment:
    """
    Loads a custom environment.
    """
    raise NotImplementedError("Implement your custom environment here.")
