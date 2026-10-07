from .base import BaseEnvironment, ProxyEnvironment, get_shared_proxy_environment
from .static_env import StaticOnlyEnvironment
ENVIRONMENTS = {"static-only": StaticOnlyEnvironment, "static": StaticOnlyEnvironment}
def build_environment(cfg, target_path=""):
    if cfg.get("type", "static-only") not in ENVIRONMENTS:
        raise ValueError("This deployment only permits static analysis")
    return StaticOnlyEnvironment(target_path)
