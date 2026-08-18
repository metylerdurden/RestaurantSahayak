"""Domain service layer — implemented starting Phase 3.

Services hold business rules and call repositories; they never construct SQL directly
and never call ApprovalService/EventBus from outside this layer.
"""
