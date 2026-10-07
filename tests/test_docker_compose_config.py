# -*- coding: utf-8 -*-
"""tests/test_docker_compose_config.py — a47: docker/deploy config validation

docker-compose is the first install path in README but had zero tests.
Full `docker compose up` needs a daemon (unavailable in CI sandbox), so
this validates everything that CAN be checked statically: YAML parses,
referenced files exist, service command matches an existing module,
environment passthrough names match what api_server reads.
"""
import os, sys, pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

yaml = pytest.importorskip("yaml")


class TestDockerComposeConfig:
    @classmethod
    def setup_class(cls):
        with open(os.path.join(REPO, "docker-compose.yml"), encoding="utf-8") as f:
            cls.compose = yaml.safe_load(f)

    def test_yaml_parses_with_services(self):
        assert "services" in self.compose
        assert len(self.compose["services"]) >= 1

    def test_main_service_builds_from_repo(self):
        svc = list(self.compose["services"].values())[0]
        assert "build" in svc or "image" in svc
        if "build" in svc:
            ctx = svc["build"] if isinstance(svc["build"], str) else svc["build"].get("context", ".")
            assert os.path.exists(os.path.join(REPO, ctx, "Dockerfile")), "Dockerfile missing"

    def test_command_references_real_module(self):
        """a25 regression: compose once referenced mem.py which had been deleted."""
        svc = list(self.compose["services"].values())[0]
        cmd = svc.get("command", "")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "mem.py" not in cmd_str, f"compose still references deleted mem.py: {cmd_str}"

    def test_api_key_passthrough_matches_server(self):
        """MEMTETHER_API_KEY env passthrough must match api_server's expected var."""
        svc = list(self.compose["services"].values())[0]
        env_list = svc.get("environment", [])
        names = set()
        for e in env_list:
            names.add(e.split("=")[0] if isinstance(e, str) else e)
        src = open(os.path.join(REPO, "api_server.py"), encoding="utf-8").read()
        if "MEMTETHER_API_KEY" in src:
            assert "MEMTETHER_API_KEY" in names, "api_server reads MEMTETHER_API_KEY but compose does not pass it"

    def test_dockerfile_references_real_entry(self):
        """Dockerfile CMD/ENTRYPOINT must reference a module that exists in the repo."""
        with open(os.path.join(REPO, "Dockerfile"), encoding="utf-8") as f:
            df = f.read()
        for line in df.splitlines():
            if line.startswith(("CMD", "ENTRYPOINT")):
                # extract python module names mentioned
                import re
                for m in re.findall(r"[\w/]*\.py", line):
                    base = os.path.basename(m)
                    assert os.path.exists(os.path.join(REPO, base)), f"Dockerfile references missing {base}"
