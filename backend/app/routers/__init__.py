"""路由汇总：业务路由按领域拆分；通用资源路由最后注册。"""
from app.routers import resource, segment_delegation

ROUTERS = [segment_delegation, resource]
