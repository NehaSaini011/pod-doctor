"""Example pasted output for the 'Paste your own output' tab (a fictional pod, for demonstration)."""

EXAMPLE_DESCRIBE = """Name:             checkout-service-7d4b9c6f5-x2kqp
Namespace:        payments
Priority:         0
Node:             worker-2/10.0.1.12
Start Time:       Fri, 20 Nov 2026 09:14:02 +0530
Labels:           app=checkout-service
                  pod-template-hash=7d4b9c6f5
Status:           Running
IP:               10.244.2.31
Controlled By:    ReplicaSet/checkout-service-7d4b9c6f5
Containers:
  checkout:
    Container ID:   containerd://3f1a9c...
    Image:          registry.example.com/checkout-service:2.14.0
    Port:           8080/TCP
    State:          Running
      Started:      Fri, 20 Nov 2026 09:31:47 +0530
    Last State:     Terminated
      Reason:       Error
      Exit Code:    143
      Started:      Fri, 20 Nov 2026 09:29:12 +0530
      Finished:     Fri, 20 Nov 2026 09:31:46 +0530
    Ready:          False
    Restart Count:  9
    Limits:
      cpu:     1
      memory:  1Gi
    Requests:
      cpu:      500m
      memory:   768Mi
    Liveness:   http-get http://:8080/actuator/health delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/actuator/health/readiness delay=5s timeout=1s period=5s #success=1 #failure=3
    Environment:
      SPRING_PROFILES_ACTIVE:  prod
      DB_HOST:                 checkout-db.payments.svc.cluster.local
Conditions:
  Type              Status
  Initialized       True
  Ready             False
  ContainersReady   False
  PodScheduled      True
Events:
  Type     Reason     Age                   From     Message
  ----     ------     ----                  ----     -------
  Warning  Unhealthy  4m (x27 over 17m)     kubelet  Liveness probe failed: Get "http://10.244.2.31:8080/actuator/health": context deadline exceeded (Client.Timeout exceeded while awaiting headers)
  Normal   Killing    4m (x9 over 17m)      kubelet  Container checkout failed liveness probe, will be restarted
  Warning  Unhealthy  3m (x41 over 17m)     kubelet  Readiness probe failed: Get "http://10.244.2.31:8080/actuator/health/readiness": dial tcp 10.244.2.31:8080: connect: connection refused
  Warning  BackOff    2m (x12 over 15m)     kubelet  Back-off restarting failed container checkout in pod checkout-service-7d4b9c6f5-x2kqp
"""
