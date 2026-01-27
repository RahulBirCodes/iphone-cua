## iPhoneEnv

- vm_name not needed
- vm_process??? don't get why this is needed
- collect_rollout needs to take in an initial prompt with what the task is + setup initial turn with prompt and then initial screenshot
- also create dummy parse and judge functions (maybe you pass into the init), if parsing fails, then give slight negative reward for that turn and continue. If the agent parsed_action is done (read vm_controller to see actions agent can take) or agent has reached max_turns, run judge function and add reward and return.
- if \_send_action returns an error in the response (not that the vm controller is down), include that error in environment response to let the llm agent learn how to recover. HOWEVER IF IT'S AN ERROR BECAUSE THE VM_CONTROLLER COULD NOT GET THE RESPONSE, then crash the actor
- when restarting vm, poll somehow by either waiting for ip to be up from tart or see if it's os can run commands yet (whatever is more robust) and if it goes over the deadline, quit it and crash the actor and let ray restart it
- also ensure what kind of screenshot from vm_controller it returns so that you can see if you really need \_decode_screenshot
