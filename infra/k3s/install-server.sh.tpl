#!/bin/sh
# Installs k3s as the cluster's one control-plane ("master") node. Nothing in
# this file names a cloud -- it only uses the k3s installer and a token, both
# of which work identically on a VM anywhere. The cloud-specific part is
# entirely in whichever Terraform module renders this template (today
# deploy/azure/terraform, via cloud-init) and supplies the two values below.
#
# Traefik and ServiceLB (k3s's bundled ingress controller and load-balancer)
# are disabled: infra/k8s/base/31-review-console-ingress.yaml already assumes
# ingress-nginx, "the portable choice" per its own header -- installing it is
# a separate, deliberate step, not a side effect of installing Kubernetes.
set -eu

# The cluster runs its own NFS server for the shared pcdf-data volume
# (infra/k8s/base/09-nfs-server.yaml), and k3s does not taint this node --
# any pod, including one needing that mount, can land here. Without the NFS
# client package, that mount fails forever with "bad option; ... you might
# need a /sbin/mount.<type> helper program", not a message that says what's
# missing.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nfs-common

curl -sfL https://get.k3s.io | \
  INSTALL_K3S_EXEC="server --disable traefik --disable servicelb --tls-san ${public_ip}" \
  K3S_TOKEN="${k3s_token}" \
  sh -

# Readable by the admin user so `k3s kubectl` and a copied-out kubeconfig both
# work without sudo.
mkdir -p /home/${admin_username}/.kube
cp /etc/rancher/k3s/k3s.yaml /home/${admin_username}/.kube/config
sed -i "s/127.0.0.1/${public_ip}/" /home/${admin_username}/.kube/config
chown -R ${admin_username}:${admin_username} /home/${admin_username}/.kube
