"""
Architecture:
- Each aws ec2 instance has a control agent running on it with tart installed:
  - todo: create script that installs tart and spins this up => I should probably create an image with this installed already
- the control agent is reponsible for 


- outer abstraction: create a class called PoolSimManager that exposes an interface that allows you to init and start a new rollout and keep getting steps
- outer abstractions: PoolManager, IphoneEnv
- each 

- remote agent is running on each VM and is only ressponsible for getting commands send and running them on the vm

- the control agent is running on the actual macos machine (each ec2 instance) and is responsible for:
1. create a golden image with xcode/sim/tart/remote agent installed + permissions given to use screen

"""