"""OneWooz edge failover: keeps every custom-domain IP on a healthy Caddy edge.

Runs every minute (EventBridge). There are two edges, onewooz-edge-a (zone 1a)
and onewooz-edge-b (zone 1b), each in a one-server Auto Scaling group, and two
Elastic IPs. SSM /onewooz/prod/edge/<role>/eip-alloc says which IP belongs to
which edge ("none" while a role has no IP yet).

For each IP:
  * On its home edge and answering         -> nothing to do.
  * On its home edge and NOT answering     -> move it to the other edge's
    spare private address (each edge has one), so that edge serves both IPs.
  * On the other edge, home healthy again  -> move it back home.
  * Not attached anywhere                  -> attach to home if healthy, else
    to the other edge.

"Answering" means Caddy replies on :80 (it always redirects to HTTPS), checked
twice a few seconds apart so one dropped packet never triggers a move. The
replacement server's boot script also reclaims its IP, so this function only
has to cover the few minutes a dead server takes to be replaced.

Publishes OneWooz/Edge Healthy (1 or 0) per role for alarms.
"""

import http.client
import time

import boto3

REGION = "ap-south-1"
ROLES = ("a", "b")
GROUP = "onewooz-edge-{}"
PARAM = "/onewooz/prod/edge/{}/eip-alloc"

ec2 = boto3.client("ec2", region_name=REGION)
asg = boto3.client("autoscaling", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)


def answers(ip, tries=2, gap=4):
    """True when Caddy at ``ip`` responds on :80 in any of ``tries`` attempts."""
    if not ip:
        return False
    for attempt in range(tries):
        try:
            conn = http.client.HTTPConnection(ip, 80, timeout=3)
            conn.request("GET", "/", headers={"Host": "edge-health.onewooz.internal"})
            status = conn.getresponse().status
            conn.close()
            if 200 <= status < 500:
                return True
        except OSError:
            pass
        if attempt + 1 < tries:
            time.sleep(gap)
    return False


def edge(role):
    """The InService instance of an edge group with its network details."""
    groups = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[GROUP.format(role)])
    ids = [
        i["InstanceId"]
        for g in groups["AutoScalingGroups"]
        for i in g["Instances"]
        if i["LifecycleState"] == "InService" and i["HealthStatus"] == "Healthy"
    ]
    if not ids:
        return None
    inst = ec2.describe_instances(InstanceIds=ids[:1])["Reservations"][0]["Instances"][0]
    if inst["State"]["Name"] != "running":
        return None
    eni = inst["NetworkInterfaces"][0]
    addrs = eni["PrivateIpAddresses"]
    primary = next(a for a in addrs if a["Primary"])
    spare = next((a for a in addrs if not a["Primary"]), None)
    return {
        "id": inst["InstanceId"],
        "eni": eni["NetworkInterfaceId"],
        "primary": primary["PrivateIpAddress"],
        "primary_public": (primary.get("Association") or {}).get("PublicIp"),
        "spare": spare["PrivateIpAddress"] if spare else None,
    }


def move(alloc, target, private_ip, why):
    ec2.associate_address(
        AllocationId=alloc,
        NetworkInterfaceId=target["eni"],
        PrivateIpAddress=private_ip,
        AllowReassociation=True,
    )
    print(f"moved {alloc} -> {target['id']} {private_ip}: {why}")


def handler(event, context):
    edges = {r: edge(r) for r in ROLES}
    report = {}
    for role in ROLES:
        other = "b" if role == "a" else "a"
        try:
            alloc = ssm.get_parameter(Name=PARAM.format(role))["Parameter"]["Value"]
        except ssm.exceptions.ParameterNotFound:
            continue
        if not alloc or alloc == "none":
            continue
        addr = ec2.describe_addresses(AllocationIds=[alloc])["Addresses"][0]
        ip = addr["PublicIp"]
        home, peer = edges[role], edges[other]
        on_eni = addr.get("NetworkInterfaceId")
        on_ip = addr.get("PrivateIpAddress")

        at_home = bool(home and on_eni == home["eni"] and on_ip == home["primary"])
        at_peer = bool(peer and on_eni == peer["eni"])

        if at_home:
            healthy = answers(ip)
            if not healthy and peer and peer["spare"] and answers(peer["primary_public"]):
                move(alloc, peer, peer["spare"], f"home edge-{role} not answering")
        elif at_peer:
            healthy = answers(ip)
            # The home server comes back with a temporary public IP of its own,
            # which is how its health is checked before handing the IP back.
            if home and home["primary_public"] and home["primary_public"] != ip and answers(home["primary_public"], tries=3):
                move(alloc, home, home["primary"], f"edge-{role} healthy again")
        else:
            healthy = False
            if home and answers(home["primary_public"]):
                move(alloc, home, home["primary"], "unattached, home healthy")
            elif peer and peer["spare"] and answers(peer["primary_public"]):
                move(alloc, peer, peer["spare"], "unattached, home unavailable")
        report[role] = 1 if healthy else 0

    if report:
        cw.put_metric_data(
            Namespace="OneWooz/Edge",
            MetricData=[
                {"MetricName": "Healthy", "Dimensions": [{"Name": "Role", "Value": r}], "Value": v, "Unit": "Count"}
                for r, v in report.items()
            ],
        )
    return report
