from kubernetes import client
import logging

logger = logging.getLogger(__name__)


def create_pod(image, command, environment, name, namespace, mounts, volumes, working_dir):
    container = client.V1Container(
        name=name,
        image=image,
        volume_mounts=mounts,
        args=command.split(),
        working_dir=working_dir,
        env=[client.V1EnvVar(k, v) for k, v in environment.items()],
        security_context=client.V1SecurityContext(
            privileged=True,
        ),
    )
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            generate_name=name+'-',
            namespace=namespace,
            labels={"app": "roman"},
        ),
        spec=client.V1PodSpec(
            containers=[container],
            volumes=volumes,
            restart_policy="Never",
        )
    )
    v1 = client.CoreV1Api()
    res = v1.create_namespaced_pod(namespace, pod)
    return res
