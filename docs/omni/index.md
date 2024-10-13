# Omni

For this cluster I am taking advantage of [omni](https://omni.siderolabs.com/) from Sidero Labs to manage my Talos installation. The home-ops clusters I've reviewed rely heavily on `talosctl` to manage their clusters but I wanted to skip a few steps and for $10/month I figured it was worth a shot. After getting more familiar with Talos and Omni I do think you can live with out it but I am enjoying the auth and VPN access that comes out of the box.

## The Journey Begins

At this point I've gotten started but documenting as I go endes up being a mess so here's the goods:

![initial-omni-dash](docs/images/cluster/initial-omni-dash.png)

I'll recap how the story went so far but first some TODOs:

- Bootstraping flux sets those limits but I'm not sure why

## Rook

!!! Dependencies 

    - External Secrets (for dashboard)

A cluster insn't a cluster with out some ceph action and that's where this [rook guide](https://www.talos.dev/v1.8/kubernetes-guides/configuration/ceph-with-rook/) on the talos docs comes in. 

??? question "What does this do?"

    This seems cool but we will come back to it later.

    === "One thing"

        This is one thing to check out.

    === "A second thing"

        This is a second thing to check out.

Back to ceph! 