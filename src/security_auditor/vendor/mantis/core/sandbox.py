from core.environments import ENVIRONMENTS, StaticOnlyEnvironment, build_environment
StaticOnlySandbox = StaticOnlyEnvironment
SANDBOXES = ENVIRONMENTS
def build_sandbox(cfg, target_path=""):
    return build_environment(cfg, target_path)
