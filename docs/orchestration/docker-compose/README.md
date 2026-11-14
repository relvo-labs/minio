# Deploy Granary on Docker Compose

Docker Compose allows defining and running single host, multi-container Docker applications.

With Compose, you use a Compose file to configure MinIO services. Then, using a single command, you can create and launch all the Distributed MinIO instances from your configuration. Distributed MinIO instances will be deployed in multiple containers on the same host. This is a great way to set up development, testing, and staging environments, based on Distributed MinIO.

## 1. Prerequisites

* Familiarity with [Docker Compose](https://docs.docker.com/compose/overview/).
* Docker installed on your machine. Download the relevant installer from [here](https://www.docker.com/community-edition#/download).

## 2. Run Distributed MinIO on Docker Compose

Granary is an unofficial fork derived from MinIO. Its image is not yet published.
From this fork's repository root, build the local image before using the checked-in
Compose file and nginx configuration:

```sh
docker build -t granary-local:dev .
cd docs/orchestration/docker-compose
```

For a binary installation, use the [source quickstart](../../../README.md#build-and-try-locally),
which builds `granary`. Installing `github.com/minio/minio@latest` fetches upstream.
Self-update is disabled; replace the image manually.

### GNU/Linux and macOS

```sh
docker-compose up
```

or

```sh
docker stack deploy --compose-file docker-compose.yaml minio
```

### Windows

```sh
docker-compose.exe up
```

or

```sh
docker stack deploy --compose-file docker-compose.yaml minio
```

Distributed instances are now accessible on the host using the Minio CLI on port 9000 and the Minio Web Console on port 9001. Proceed to access the Web browser at <http://127.0.0.1:9001/>. Here 4 MinIO server instances are reverse proxied through Nginx load balancing.

### Notes

* The Compose file uses your locally built `granary-local:dev` image. Make it
  available on every node before a Swarm deployment. No Granary image is published yet.

* There are 4 minio distributed instances created by default. You can add more MinIO services (up to total 16) to your MinIO Compose deployment. To add a service
  * Replicate a service definition and change the name of the new service appropriately.
  * Update the command section in each service.
  * Add a new MinIO server instance to the upstream directive in the Nginx configuration file.

  Read more about distributed MinIO [here](https://docs.min.io/community/minio-object-store/operations/deployments/baremetal-deploy-minio-as-a-container.html).

### Explore Further

* [Overview of Docker Compose](https://docs.docker.com/compose/overview/)
* [MinIO Docker Quickstart Guide](https://docs.min.io/community/minio-object-store/operations/deployments/baremetal-deploy-minio-as-a-container.html)
* [MinIO Erasure Code QuickStart Guide](https://docs.min.io/community/minio-object-store/operations/concepts/erasure-coding.html)
