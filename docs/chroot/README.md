# Deploy Granary in a chroot

Chroot allows user based namespace isolation on many standard Linux deployments.

## 1. Prerequisites

- Familiarity with [chroot](http://man7.org/linux/man-pages/man2/chroot.2.html)
- Chroot installed on your machine.

## 2. Install MinIO in Chroot

> **Note:** MinIO community edition is now distributed as source code only. Pre-compiled binaries are no longer provided for new releases.

Build MinIO from source and install it in the chroot directory:

```sh
# From a checkout of relvo-labs/granary, with the go.mod toolchain:
CGO_ENABLED=0 GOTOOLCHAIN=local go build -o granary .

# Create the bin directory in your chroot
mkdir -p /mnt/export/${USER}/bin

# Copy the built binary to the chroot directory
cp ./granary /mnt/export/${USER}/bin/granary
chmod +x /mnt/export/${USER}/bin/granary
```

Granary is an unofficial fork derived from MinIO. Self-update is disabled;
replace the Granary binary manually. No published Granary binary is available yet.

Bind your `proc` mount to the target chroot directory

```
sudo mount --bind /proc /mnt/export/${USER}/proc
```

## 3. Run Standalone MinIO in Chroot

### GNU/Linux

```sh
sudo chroot --userspec username:group /mnt/export/${USER} /bin/granary --config-dir=/.minio server /data

Endpoint:  http://192.168.1.92:9000  http://65.19.167.92:9000
AccessKey: MVPSPBW4NP2CMV1W3TXD
SecretKey: X3RKxEeFOI8InuNWoPsbG+XEVoaJVCqbvxe+PTOa
...
...
```

Instance is now accessible on the host at port 9000, proceed to access the Web browser at <http://127.0.0.1:9000/>

## Explore Further

- [MinIO Erasure Code Overview](https://docs.min.io/community/minio-object-store/operations/concepts/erasure-coding.html)
- [Use `mc` with MinIO Server](https://docs.min.io/community/minio-object-store/reference/minio-mc.html)
- [Use `aws-cli` with MinIO Server](https://docs.min.io/community/minio-object-store/integrations/aws-cli-with-minio.html)
- [Use `minio-go` SDK with MinIO Server](https://docs.min.io/community/minio-object-store/developers/go/minio-go.html)
- [The MinIO documentation website](https://docs.min.io/community/minio-object-store/index.html)
