# Docker Hub

Publish your local Dufyn images to Docker Hub.

First, sign in to Docker Hub and create a repository named `dufyn` under your account.

The examples below use version `0.1.0`. Replace it with the version you want to publish.

## 1. Check the local images

```shell
docker images
```

Confirm that the following repositories and tags are available:

```text
REPOSITORY         TAG
dufyn-scanner      0.1.0
dufyn-manager      0.1.0
dufyn-controller   0.1.0
dufyn-database     0.1.0
```

## 2. Log in to Docker Hub

```shell
docker login
```

Follow the authentication instructions displayed in your terminal.

## 3. Tag the images

**Note:** Replace `<your-dockerhub-username>` with your Docker Hub username in all commands below, for example, `dobzinski`.

Use a component name and version to identify each image within the same repository.

```shell
docker tag dufyn-scanner:0.1.0 <your-dockerhub-username>/dufyn:scanner-0.1.0
docker tag dufyn-manager:0.1.0 <your-dockerhub-username>/dufyn:manager-0.1.0
docker tag dufyn-controller:0.1.0 <your-dockerhub-username>/dufyn:controller-0.1.0
docker tag dufyn-database:0.1.0 <your-dockerhub-username>/dufyn:database-0.1.0
```

## 4. Push the images

Push the tagged images to the `dufyn` repository you created earlier.

```shell
docker push <your-dockerhub-username>/dufyn:scanner-0.1.0
docker push <your-dockerhub-username>/dufyn:manager-0.1.0
docker push <your-dockerhub-username>/dufyn:controller-0.1.0
docker push <your-dockerhub-username>/dufyn:database-0.1.0
```

## 5. Verify the published tags

Open your `dufyn` repository on Docker Hub and confirm that its **Tags** tab lists:

- `scanner-0.1.0`
- `manager-0.1.0`
- `controller-0.1.0`
- `database-0.1.0`


## 6. Publish an updated component

After updating the Manager code, publish a new version without overwriting
the existing tag. Run the commands from the project root.

Update `deploy/docker/.env`:

```dotenv
DUFYN_MANAGER_IMAGE_TAG=0.1.1
```

Update the version field in `manager/version.yaml`, preserving its structure:

```yaml
version: "0.1.1"
```

Build the updated image:

```shell
docker compose -f deploy/docker/compose.yaml build manager
```

After the build succeeds, test the image before publishing it.

Tag and push the validated image:

```shell
docker tag dufyn-manager:0.1.1 <your-dockerhub-username>/dufyn:manager-0.1.1
docker push <your-dockerhub-username>/dufyn:manager-0.1.1
```

Update the target deployment to use the new image tag.
Other components can retain their existing versions.