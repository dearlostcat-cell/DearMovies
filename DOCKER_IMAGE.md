# Docker image with welcome video — 2.2.4

This source package includes your supplied assets/dear.mp4 (761,524 bytes). The Dockerfile copies it into /app/assets/dear.mp4. Compose mounts the included assets folder over that directory, so keep the video there when using Compose. The video is intentionally allowed into Git for this release; it will be visible if you publish a public repository.

No Docker engine was available in the preparation environment. This archive contains build inputs, not a prebuilt image.

## Build and run on your server

Follow README.md to configure .env, then:

```sh
docker compose up -d --build
```

This builds the tagged image `lost-movies:2.2.4`. To export it for another server:

```sh
docker save lost-movies:2.2.4 | gzip > lost-movies-2.2.4.tar.gz
```

## Build on GitHub without local Docker

1. Upload this source package to your GitHub repository, including the hidden .github directory and assets/dear.mp4. Never upload .env.
2. Open Actions → Build downloadable Docker image → Run workflow.
3. After a successful run, download the artifact named lost-movies-docker-image-linux-amd64. Extract its outer ZIP to obtain the .tar.gz image archive.
4. On a Linux amd64 Docker server, load it:

```sh
docker load -i lost-movies-2.2.4-linux-amd64.tar.gz
docker compose up -d --no-build
```

Keep the source folder, Compose configuration and .env on that server. The image includes the application and welcome video; PostgreSQL and optional Caddy are separate Compose services. The workflow image is Linux amd64, so ARM servers should build locally instead. The workflow uses GitHub Actions resources subject to your account allowances and does not push to a public container registry.

It tests the presence of the video and starts the webpage with no Telegram token. Successful image checks do not verify real Telegram delivery or access to blocked download hosts.
