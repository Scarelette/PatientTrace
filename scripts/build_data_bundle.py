"""Build angel-demo-data.tar.gz from the research worktree (maintainers only).

    python scripts/build_data_bundle.py <research_repo> <out_dir>
    python scripts/build_data_bundle.py --from-data <data folder> <out_dir>

Copies only what the demo reads at run time. Node embeddings are left out: the frozen F1 method
("ours") does not use retrieval, so they are never needed.
"""
import glob
import os
import shutil
import sys
import tarfile

if sys.argv[1] == "--from-data":
    # python scripts/build_data_bundle.py --from-data <data folder> <out_dir>: package a ready data folder (what
    # PATIENTTRACE_DATA points at, e.g. data-local/) as it is, minus backups (*.f1.json, *.bak*)
    data, out = os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
    os.makedirs(out, exist_ok=True)
    archive = os.path.join(out, "angel-demo-data.tar.gz")
    skip = lambda name: name.endswith(".f1.json") or ".bak" in name or name.startswith(".")
    n = 0
    with tarfile.open(archive, "w:gz") as t:
        for root, dirs, files in os.walk(data):
            dirs[:] = sorted(d for d in dirs if not skip(d))
            for f in sorted(files):
                if not skip(f):
                    full = os.path.join(root, f)
                    t.add(full, arcname=os.path.join("angel-demo-data", os.path.relpath(full, data))); n += 1
    print(f"{n} files from {data} -> {archive} ({os.path.getsize(archive) / 1e6:.1f} MB)")
    sys.exit(0)

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
