from hyperpyyaml import load_hyperpyyaml
import torch
import numpy as np

yaml_path = './pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512/cosyvoice3.yaml'

try:
    with open(yaml_path, 'r') as f:
        # Attempt to load just the hift part or whole config
        print("Loading YAML...")
        configs = load_hyperpyyaml(f, overrides={'llm': None, 'flow': None})
        print("YAML loaded successfully.")
        
        # Instantiate hift
        print("Instantiating HiFT...")
        # hift is already instantiated by load_hyperpyyaml if !new is used? 
        # Yes, !new creates the object.
        hift = configs['hift']
        print("HiFT instantiated:", hift)

except Exception as e:
    import traceback
    traceback.print_exc()
