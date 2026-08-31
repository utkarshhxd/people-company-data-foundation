#!/bin/sh
# Installs k3s as a worker node, joining the server at ${server_ip}. Same
# note as install-server.sh.tpl: nothing here is cloud-specific, only the
# values the rendering Terraform module fills in.
set -eu

# See install-server.sh.tpl's matching comment: without this, any pod
# scheduled here that needs the shared pcdf-data NFS mount fails forever.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nfs-common

curl -sfL https://get.k3s.io | \
  K3S_URL="https://${server_ip}:6443" \
  K3S_TOKEN="${k3s_token}" \
  sh -
