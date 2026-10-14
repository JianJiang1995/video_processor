"""
Load LAM_Lite pretrained weights and evaluate on OUR val set (random split).
This allows fair comparison with our Rendezvous baseline.

LAM-Lite architecture:
- ResNet-18 backbone with frozen BN + learned position embedding
- input_proj 1x1 conv
- transformer_space (2 layers) aggregates 49 spatial tokens per frame
- transformer_time (2 layers) aggregates 10 temporal tokens
- 4 classification heads: fc_i (6), fc_v (10), fc_t (15), fc_a (100)

Input: 10-frame clip, 224x224 per frame
"""

import os
import sys
import time
import csv
import argparse
import numpy as np
import torch
import torch.nn as nn
import torchvision
from torchvision.models._utils import IntermediateLayerGetter
from torchvision import transforms
from torch.utils.data import Dataset, DataLoader
from PIL import Image
from sklearn.metrics import average_precision_score
from collections import defaultdict


# ─── FrozenBatchNorm2d (from LAM / DETR) ───
class FrozenBatchNorm2d(nn.Module):
    def __init__(self, n):
        super().__init__()
        self.register_buffer("weight", torch.ones(n))
        self.register_buffer("bias", torch.zeros(n))
        self.register_buffer("running_mean", torch.zeros(n))
        self.register_buffer("running_var", torch.ones(n))

    def forward(self, x):
        w = self.weight.reshape(1, -1, 1, 1)
        b = self.bias.reshape(1, -1, 1, 1)
        rv = self.running_var.reshape(1, -1, 1, 1)
        rm = self.running_mean.reshape(1, -1, 1, 1)
        eps = 1e-5
        scale = w * (rv + eps).rsqrt()
        bias = b - rm * scale
        return x * scale + bias


# ─── Backbone with learned position encoding ───
class LAMBackbone(nn.Module):
    """ResNet-18 + learned position encoding (matches LAM's state_dict layout)."""
    def __init__(self):
        super().__init__()
        # backbone.0.body: ResNet-18 body
        resnet = torchvision.models.resnet18(weights=None, norm_layer=FrozenBatchNorm2d)
        self.body = IntermediateLayerGetter(resnet, return_layers={"layer4": "0"})
        self.num_channels = 512
        # backbone.1.pe: learned position encoding (unused in forward but stored)
        self.pe_channels = 2048

    def forward(self, x):
        # Returns list with last element having shape [B, 512, H/32, W/32]
        out = self.body(x)
        return out["0"]


# ─── TAggregate (transformer aggregator) ───
class TAggregate(nn.Module):
    def __init__(self, clip_length, embed_dim, n_layers=2):
        super().__init__()
        self.embed_dim = embed_dim
        enc_layer = nn.TransformerEncoderLayer(d_model=embed_dim, nhead=8)
        self.transformer_enc = nn.TransformerEncoder(enc_layer, num_layers=n_layers, norm=nn.LayerNorm(embed_dim))
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, clip_length + 1, embed_dim))

    def forward(self, x):
        # x: [bs, clip_length, dim]
        nvids = x.shape[0]
        cls_tokens = self.cls_token.expand(nvids, -1, -1)
        x = torch.cat((cls_tokens, x), dim=1)
        x = x + self.pos_embed
        x = x.transpose(0, 1)  # seq_first for transformer
        o = self.transformer_enc(x)
        return o[0]  # return cls token output


# ─── LAM-Lite model ───
class LAMLite(nn.Module):
    def __init__(self, hidden_dim=512, num_frames=10):
        super().__init__()
        # backbone is registered as a Sequential with [body_wrapper, pe_wrapper]
        # In the state dict: backbone.0.body.* and backbone.1.pe
        # We just need body (the pe is unused in forward pass anyway)
        self.num_frames = num_frames
        self.dim_t = hidden_dim

        # Build as nn.ModuleList to match state_dict prefix "backbone.0" / "backbone.1"
        self.backbone = nn.ModuleList([
            self._make_body(),
            self._make_pe_wrapper(),
        ])
        self.input_proj = nn.Conv2d(512, hidden_dim, kernel_size=1)
        self.transformer_space = TAggregate(clip_length=7 * 7, embed_dim=hidden_dim, n_layers=2)
        self.transformer_time = TAggregate(clip_length=num_frames, embed_dim=hidden_dim, n_layers=2)
        self.fc_i = nn.Linear(hidden_dim, 6, bias=False)
        self.fc_v = nn.Linear(hidden_dim, 10, bias=False)
        self.fc_t = nn.Linear(hidden_dim, 15, bias=False)
        self.fc_a = nn.Linear(hidden_dim, 100, bias=False)

    @staticmethod
    def _make_body():
        resnet = torchvision.models.resnet18(weights=None, norm_layer=FrozenBatchNorm2d)
        body = IntermediateLayerGetter(resnet, return_layers={"layer4": "0"})
        wrapper = nn.Module()
        wrapper.body = body
        return wrapper

    @staticmethod
    def _make_pe_wrapper():
        wrapper = nn.Module()
        wrapper.pe = nn.Parameter(torch.zeros(1, 2048, 7, 7))
        return wrapper

    def forward(self, input):
        # input: [B, T, 3, H, W] or [B*T, 3, H, W]
        b, t, _, h, w = input.shape
        input = input.view(-1, 3, h, w)

        # Pass through body
        feat = self.backbone[0].body(input)["0"]  # [B*T, 512, H/32, W/32]
        src = self.input_proj(feat)  # [B*T, 512, 7, 7]

        # Space attention over 49 tokens per frame
        bt, d, hh, ww = src.shape
        src_s_in = src.contiguous().view(bt, d, -1).transpose(1, 2)  # [B*T, 49, 512]
        src_s = self.transformer_space(src_s_in)  # [B*T, 512]

        # Time attention over T frames
        src_t_in = src_s.contiguous().view(b, t, d)  # [B, T, 512]
        src_st = self.transformer_time(src_t_in)  # [B, 512]

        # Heads (following LAM's forward logic)
        # fc_i, fc_t use per-frame features (last frame of each clip as per LAM code: src_s[t-1::t])
        # Actually LAM uses src_s[t-1::t] which picks every t-th frame starting from index t-1
        # For single clip of t frames, this picks only the last frame
        last_frame_feat = src_s[t-1::t]  # [B, 512]
        return {
            "i": self.fc_i(last_frame_feat),  # [B, 6]
            "v": self.fc_v(src_st),            # [B, 10]
            "t": self.fc_t(last_frame_feat),   # [B, 15]
            "ivt": self.fc_a(src_st),          # [B, 100]
        }


# ─── Dataset: 10-frame video clips from our val set ───
class ClipDataset(Dataset):
    """
    Build 10-frame clips from our cholect50_triplet val set.
    For each frame in val.csv, use the previous 9 frames + current as a clip.
    If frame doesn't have enough history, repeat first frame.
    """
    def __init__(self, csv_path, transform=None, num_frames=10):
        self.transform = transform
        self.num_frames = num_frames

        # Organize by video
        video_frames = defaultdict(list)
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                img_path = row["image_path"]
                # Parse video_id from path (CholecT50: .../VIDxx/000000.png)
                parts = img_path.split("/")
                vid = parts[-2]
                frame_id = int(parts[-1].replace(".png", ""))

                # Parse labels
                def parse_ids(s, n):
                    vec = np.zeros(n, dtype=np.float32)
                    if s:
                        for idx in s.split(";"):
                            if idx:
                                vec[int(idx)] = 1.0
                    return vec

                video_frames[vid].append({
                    "img_path": img_path,
                    "frame_id": frame_id,
                    "i": parse_ids(row["instrument_ids"], 6),
                    "v": parse_ids(row["verb_ids"], 10),
                    "t": parse_ids(row["target_ids"], 15),
                    "ivt": parse_ids(row["triplet_ids"], 100),
                })

        # Sort each video by frame_id
        for vid in video_frames:
            video_frames[vid].sort(key=lambda x: x["frame_id"])

        # Build clips - each sample is (clip_paths, labels of LAST frame)
        self.samples = []
        for vid, frames in video_frames.items():
            for i, curr in enumerate(frames):
                # Get 10 preceding frames (including current)
                clip_paths = []
                for j in range(num_frames):
                    idx = max(0, i - (num_frames - 1) + j)
                    clip_paths.append(frames[idx]["img_path"])
                self.samples.append({
                    "clip_paths": clip_paths,
                    "i": curr["i"],
                    "v": curr["v"],
                    "t": curr["t"],
                    "ivt": curr["ivt"],
                    "vid": vid,
                    "frame_id": curr["frame_id"],
                })

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        imgs = []
        for p in s["clip_paths"]:
            try:
                img = Image.open(p).convert("RGB")
            except Exception:
                img = Image.new("RGB", (224, 224))
            if self.transform:
                img = self.transform(img)
            imgs.append(img)
        clip = torch.stack(imgs, dim=0)  # [T, 3, H, W]
        return (clip,
                torch.from_numpy(s["i"]),
                torch.from_numpy(s["v"]),
                torch.from_numpy(s["t"]),
                torch.from_numpy(s["ivt"]))


def compute_map(preds, labels):
    aps = []
    for c in range(labels.shape[1]):
        if labels[:, c].sum() == 0:
            continue
        aps.append(average_precision_score(labels[:, c], preds[:, c]))
    return np.mean(aps) if aps else 0.0, aps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=str,
        default="LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar")
    parser.add_argument("--val_csv", type=str,
        default="datasets/cholect50_triplet/val.csv")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    print(f"Loading LAM-Lite from: {args.ckpt}")
    ckpt = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    print(f"  Paper reported best_mAP: {ckpt.get('best_mAP')}")

    # Build model
    model = LAMLite(hidden_dim=512, num_frames=10)
    load_result = model.load_state_dict(ckpt["state_dict"], strict=False)
    print(f"  Missing keys: {len(load_result.missing_keys)}")
    print(f"  Unexpected keys: {len(load_result.unexpected_keys)}")
    if load_result.missing_keys[:3]:
        print(f"  Sample missing: {load_result.missing_keys[:3]}")
    if load_result.unexpected_keys[:3]:
        print(f"  Sample unexpected: {load_result.unexpected_keys[:3]}")

    model = model.cuda(args.device)
    model.eval()

    # Build dataset
    transform = transforms.Compose([
        transforms.Resize((250, 250)),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    print(f"\nLoading val set from: {args.val_csv}")
    dataset = ClipDataset(args.val_csv, transform=transform, num_frames=10)
    print(f"  Total clips: {len(dataset)}")
    loader = DataLoader(dataset, batch_size=args.batch, shuffle=False,
                        num_workers=args.workers, pin_memory=True)

    # Inference
    print("\nRunning inference...")
    all_pi, all_pv, all_pt, all_pivt = [], [], [], []
    all_li, all_lv, all_lt, all_livt = [], [], [], []

    with torch.no_grad():
        for batch_idx, (clips, li, lv, lt, livt) in enumerate(loader):
            clips = clips.cuda(args.device, non_blocking=True)
            out = model(clips)
            all_pi.append(torch.sigmoid(out["i"]).cpu().numpy())
            all_pv.append(torch.sigmoid(out["v"]).cpu().numpy())
            all_pt.append(torch.sigmoid(out["t"]).cpu().numpy())
            all_pivt.append(torch.sigmoid(out["ivt"]).cpu().numpy())
            all_li.append(li.numpy())
            all_lv.append(lv.numpy())
            all_lt.append(lt.numpy())
            all_livt.append(livt.numpy())

            if (batch_idx + 1) % 50 == 0:
                print(f"  {batch_idx + 1}/{len(loader)}")

    all_pi = np.concatenate(all_pi)
    all_pv = np.concatenate(all_pv)
    all_pt = np.concatenate(all_pt)
    all_pivt = np.concatenate(all_pivt)
    all_li = np.concatenate(all_li)
    all_lv = np.concatenate(all_lv)
    all_lt = np.concatenate(all_lt)
    all_livt = np.concatenate(all_livt)

    # Metrics
    map_i, _ = compute_map(all_pi, all_li)
    map_v, _ = compute_map(all_pv, all_lv)
    map_t, _ = compute_map(all_pt, all_lt)
    map_ivt, _ = compute_map(all_pivt, all_livt)

    print(f"\n=== LAM-Lite on OUR val set ===")
    print(f"  mAP-I:   {map_i:.4f}")
    print(f"  mAP-V:   {map_v:.4f}")
    print(f"  mAP-T:   {map_t:.4f}")
    print(f"  mAP-IVT: {map_ivt:.4f}")

    # Speed benchmark
    print("\nBenchmarking inference speed...")
    model.eval()
    dummy = torch.randn(1, 10, 3, 224, 224).cuda(args.device)
    for _ in range(5):
        model(dummy)
    torch.cuda.synchronize()
    t0 = time.time()
    for _ in range(50):
        model(dummy)
    torch.cuda.synchronize()
    fps = 50 / (time.time() - t0)
    print(f"  Inference: {fps:.1f} clips/s (per-clip = 10 frames)")
    print(f"  Effective: {fps * 10:.0f} FPS (frames/s)")


if __name__ == "__main__":
    main()
