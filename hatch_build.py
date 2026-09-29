"""Compiles the Flipper protobuf definitions in proto/ into src/flipperzero_ble/_proto/ at build time."""

import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ProtoBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        root = Path(self.root)
        proto_dir = root / "proto"
        out_dir = root / "src" / "flipperzero_ble" / "_proto"
        protos = sorted(proto_dir.glob("*.proto"))
        if not protos:
            if any(out_dir.glob("*_pb2.py")):
                return
            raise RuntimeError("proto/ is empty; run `git submodule update --init` first")

        import grpc_tools
        from grpc_tools import protoc

        well_known = Path(grpc_tools.__file__).parent / "_proto"
        out_dir.mkdir(parents=True, exist_ok=True)
        args = ["protoc", f"-I{proto_dir}", f"-I{well_known}", f"--python_out={out_dir}", *map(str, protos)]
        if protoc.main(args) != 0:
            raise RuntimeError("protoc failed to compile proto/*.proto")

        # protoc emits top-level imports between generated modules; make them package-relative.
        for module in out_dir.glob("*_pb2.py"):
            text = module.read_text()
            module.write_text(re.sub(r"^import (\w+_pb2) as", r"from . import \1 as", text, flags=re.M))
