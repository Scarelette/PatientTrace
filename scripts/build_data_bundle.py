"""Build angel-demo-data.tar.gz from the research worktree (maintainers only).

    python scripts/build_data_bundle.py <research_repo> <out_dir>

Copies only what the demo reads at run time. Node embeddings are left out: the frozen F1 method
("ours") does not use retrieval, so they are never needed.
"""
import glob
import os
import shutil
import sys
import tarfile

src, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
stage = os.path.join(out, "angel-demo-data")
if os.path.exists(stage):
    shutil.rmtree(stage)
os.makedirs(stage)


def cp(rel_src, rel_dst=None):
    a, b = os.path.join(src, rel_src), os.path.join(stage, rel_dst or rel_src)
    os.makedirs(os.path.dirname(b), exist_ok=True)
    shutil.copy2(a, b)


for f in ("patients_adult.jsonl", "splits.json", "f2_targets.json"):
    cp(os.path.join("data", f), f)
for f in sorted(glob.glob(os.path.join(src, "data", "concepts", "*.json"))):
    cp(os.path.relpath(f, src), os.path.join("concepts", os.path.basename(f)))
for f in sorted(glob.glob(os.path.join(src, "data", "replay", "*.json"))):
    cp(os.path.relpath(f, src), os.path.join("replay", os.path.basename(f)))
for name in ("all", "dev", "test"):
    cp(os.path.join("outputs", "f2", "ours", f"{name}.jsonl"), os.path.join("f2", "ours", f"{name}.jsonl"))
for f in sorted(glob.glob(os.path.join(src, "methods", "f2_recommend", "cache", "*.json"))):
    cp(os.path.relpath(f, src), os.path.join("f2_cache", os.path.basename(f)))
n = 0
for d in sorted(glob.glob(os.path.join(src, "data", "precomputed", "p[0-9][0-9][0-9]"))):
    pid = os.path.basename(d)
    for f in ("network.json", "long_profile.json", "raw_network.json", "groups.json", "meta.json"):
        if os.path.exists(os.path.join(d, f)):
            cp(os.path.join("data", "precomputed", pid, f), os.path.join("precomputed", pid, f))
    n += 1
archive = os.path.join(out, "angel-demo-data.tar.gz")
with tarfile.open(archive, "w:gz") as t:
    t.add(stage, arcname="angel-demo-data")
print(f"{n} patients -> {archive} ({os.path.getsize(archive) / 1e6:.1f} MB)")
