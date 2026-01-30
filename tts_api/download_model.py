from modelscope import snapshot_download

model_dir = snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', cache_dir='pretrained_models')
print(f"Model downloaded to: {model_dir}")
