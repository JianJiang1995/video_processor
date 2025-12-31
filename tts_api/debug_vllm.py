import vllm
import importlib
import pkgutil
import sys

print(f"vLLM version: {vllm.__version__}")

def find_class(package, class_name):
    path = package.__path__
    prefix = package.__name__ + "."

    for _, name, ispkg in pkgutil.walk_packages(path, prefix):
        if "test" in name: continue
        try:
            module = importlib.import_module(name)
            if hasattr(module, class_name):
                print(f"Found {class_name} in {name}")
                return name
        except Exception:
            pass
    return None

find_class(vllm, "SamplingMetadata")
