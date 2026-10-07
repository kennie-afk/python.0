#!/usr/bin/env python3
"""Static checks on aegis/k8s: the mistakes that only show up when a pod refuses to start or a policy silently does nothing.

It cannot replace applying the manifests to a real cluster (see docs/DEPLOY.md for what was and was not applied).
Exit status 1 when any check fails.
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
errors = []
def fail(msg): errors.append(msg)

docs = []
for path in sorted(glob.glob(os.path.join(ROOT, "k8s", "*.yaml"))):
    if path.endswith("02-secret.example.yaml") or path.endswith("kustomization.yaml"):
        continue
    for d in yaml.safe_load_all(open(path)):
        if d:
            d["_file"] = os.path.basename(path)
            docs.append(d)
secret = next(yaml.safe_load_all(open(os.path.join(ROOT, "k8s", "02-secret.example.yaml"))))
secret_keys = set(secret["stringData"])
config = next(d for d in docs if d["kind"] == "ConfigMap")
config_keys = set(config["data"])
by_kind = lambda k: [d for d in docs if d["kind"] == k]
name = lambda d: f'{d["kind"]}/{d["metadata"]["name"]}'

# every namespaced object says which namespace, and it is the one the Namespace creates
ns = by_kind("Namespace")[0]["metadata"]["name"]
for d in docs:
    if d["kind"] != "Namespace" and d["metadata"].get("namespace") != ns:
        fail(f"{name(d)}: namespace is not {ns}")
if by_kind("Namespace")[0]["metadata"]["labels"].get("pod-security.kubernetes.io/enforce") != "restricted":
    fail("Namespace does not enforce the restricted pod security profile")

# the user each image runs as, from its Dockerfile
def dockerfile_user(rel):
    for line in open(os.path.join(ROOT, rel)).read().splitlines()[::-1]:
        m = re.match(r"USER\s+(\d+)(?::(\d+))?\s*$", line.strip())
        if m:
            return int(m.group(1))
    return None
image_uid = {"api": dockerfile_user("Dockerfile"), "console": dockerfile_user("apps/console/Dockerfile")}
for k, v in image_uid.items():
    if v is None:
        fail(f"{k}: the Dockerfile's last USER is not numeric, so Kubernetes cannot verify it is non-root")

workloads = by_kind("Deployment") + by_kind("StatefulSet") + by_kind("Job") + by_kind("CronJob")
long_running = ("Deployment", "StatefulSet")
def template(w):
    return w["spec"]["jobTemplate"]["spec"]["template"] if w["kind"] == "CronJob" else w["spec"]["template"]
pod_labels = {}
for w in workloads:
    spec = template(w)["spec"]
    labels = template(w)["metadata"]["labels"]
    pod_labels[name(w)] = labels
    if w["kind"] in long_running:
        sel = w["spec"]["selector"]["matchLabels"]
        if any(labels.get(k) != v for k, v in sel.items()):
            fail(f"{name(w)}: selector does not match the pod labels")
    ps = spec.get("securityContext", {})
    if not ps.get("runAsNonRoot") or not isinstance(ps.get("runAsUser"), int) or ps.get("runAsUser") == 0:
        fail(f"{name(w)}: pod must set runAsNonRoot and a numeric non-zero runAsUser")
    if ps.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        fail(f"{name(w)}: seccompProfile must be RuntimeDefault")
    if spec.get("automountServiceAccountToken") is not False:
        fail(f"{name(w)}: automountServiceAccountToken must be false")
    short = w["metadata"]["name"]
    if short in image_uid and image_uid[short] is not None and ps.get("runAsUser") != image_uid[short]:
        fail(f"{name(w)}: runAsUser {ps.get('runAsUser')} differs from the Dockerfile's USER {image_uid[short]}")
    for c in [dict(i, _init=True) for i in spec.get("initContainers", [])] + spec["containers"]:
        init = c.get("_init", False)
        who = f'{name(w)}/{c["name"]}'
        img = c["image"]
        if ":" not in img.split("/")[-1] or img.endswith(":latest"):
            fail(f"{who}: image {img} needs a pinned tag, not latest or none")
        r = c.get("resources", {})
        for part in ("requests", "limits"):
            for res in ("cpu", "memory"):
                if res not in r.get(part, {}):
                    fail(f"{who}: resources.{part}.{res} missing")
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            if probe not in c and not init and w["kind"] in long_running:
                fail(f"{who}: {probe} missing")
        sc = c.get("securityContext", {})
        if sc.get("allowPrivilegeEscalation") is not False:
            fail(f"{who}: allowPrivilegeEscalation must be false")
        if "ALL" not in sc.get("capabilities", {}).get("drop", []):
            fail(f"{who}: capabilities must drop ALL")
        port_names = {p["name"] for p in c.get("ports", []) if "name" in p}
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            p = c.get(probe, {}).get("httpGet")
            if p and isinstance(p["port"], str) and p["port"] not in port_names:
                fail(f"{who}: {probe} names port {p['port']} the container does not declare")
        # every environment reference must resolve
        for ef in c.get("envFrom", []):
            if "configMapRef" in ef and ef["configMapRef"]["name"] != config["metadata"]["name"]:
                fail(f"{who}: unknown ConfigMap {ef['configMapRef']['name']}")
            if "secretRef" in ef and ef["secretRef"]["name"] != secret["metadata"]["name"]:
                fail(f"{who}: unknown Secret {ef['secretRef']['name']}")
        for e in c.get("env", []):
            vf = e.get("valueFrom", {})
            if "configMapKeyRef" in vf and vf["configMapKeyRef"]["key"] not in config_keys:
                fail(f"{who}: ConfigMap key {vf['configMapKeyRef']['key']} does not exist")
            if "secretKeyRef" in vf and vf["secretKeyRef"]["key"] not in secret_keys and not vf["secretKeyRef"].get("optional"):
                fail(f"{who}: Secret key {vf['secretKeyRef']['key']} is not in the example Secret")
        if c.get("securityContext", {}).get("readOnlyRootFilesystem") and not init:
            mounted = {m["mountPath"] for m in c.get("volumeMounts", [])}
            if not any(p in mounted for p in ("/tmp", "/var/lib/postgresql/data")):
                fail(f"{who}: read-only root filesystem with no writable /tmp")
    for v in spec.get("volumes", []):
        if "secret" in v or "configMap" in v:
            pass

# the API must be given every secret the application cannot start without
required_secret = {"AEGIS_DB_OWNER_PASSWORD", "AEGIS_APP_PASSWORD", "AEGIS_JWT_SECRET", "AEGIS_ANONYMISATION_SALT"}
if not required_secret <= secret_keys:
    fail(f"example Secret lacks {sorted(required_secret - secret_keys)}")

# Services, HPAs, PDBs, Ingresses point at things that exist
services = {s["metadata"]["name"]: s for s in by_kind("Service")}
for s in services.values():
    sel = s["spec"]["selector"]
    if not any(all(l.get(k) == v for k, v in sel.items()) for l in pod_labels.values()):
        fail(f"{name(s)}: selector matches no workload")
workload_names = {(w["kind"], w["metadata"]["name"]) for w in workloads}
for h in by_kind("HorizontalPodAutoscaler"):
    t = h["spec"]["scaleTargetRef"]
    if (t["kind"], t["name"]) not in workload_names:
        fail(f"{name(h)}: targets {t['kind']}/{t['name']} which does not exist")
    if h["spec"]["minReplicas"] < 2:
        fail(f"{name(h)}: minReplicas below 2 leaves no redundancy")
for p in by_kind("PodDisruptionBudget"):
    sel = p["spec"]["selector"]["matchLabels"]
    if not any(all(l.get(k) == v for k, v in sel.items()) for l in pod_labels.values()):
        fail(f"{name(p)}: selector matches no workload")
for ing in by_kind("Ingress"):
    for rule in ing["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            svc = path["backend"]["service"]
            if svc["name"] not in services:
                fail(f"{name(ing)}: backend service {svc['name']} does not exist")
            elif svc["port"]["number"] not in [p["port"] for p in services[svc["name"]]["spec"]["ports"]]:
                fail(f"{name(ing)}: service {svc['name']} has no port {svc['port']['number']}")
            if path["path"].startswith("/actuator"):
                fail(f"{name(ing)}: exposes the actuator")
    for t in ing["spec"].get("tls", []):
        if not t.get("secretName"):
            fail(f"{name(ing)}: tls without a secretName")

# network policies: default deny, and every workload is selected by at least one allow rule
policies = by_kind("NetworkPolicy")
if not any(p["spec"]["podSelector"] == {} and set(p["spec"]["policyTypes"]) >= {"Ingress", "Egress"} and not p["spec"].get("ingress") and not p["spec"].get("egress") for p in policies):
    fail("no default-deny NetworkPolicy for ingress and egress")
for key, labels in pod_labels.items():
    selected = [p for p in policies if p["spec"]["podSelector"].get("matchLabels") and all(labels.get(k) == v for k, v in p["spec"]["podSelector"]["matchLabels"].items())]
    if not selected:
        fail(f"{key}: no NetworkPolicy allows it any traffic")
for p in policies:
    for rule in p["spec"].get("egress", []) + p["spec"].get("ingress", []):
        for peer in rule.get("to", []) + rule.get("from", []):
            ml = peer.get("podSelector", {}).get("matchLabels")
            if ml and not any(all(l.get(k) == v for k, v in ml.items()) for l in pod_labels.values()):
                fail(f"{name(p)}: peer {ml} matches no workload")

if errors:
    print("\n".join(f"FAIL {e}" for e in errors))
    sys.exit(1)
print(f"k8s checks passed: {len(docs)} objects, {len(workloads)} workloads, {len(policies)} network policies")
