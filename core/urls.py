from django.urls import path
from core import views

urlpatterns = [
    # Main Corridor Schedule Dashboard (TMS, SMMS, TRD)
    path('', views.index, name='index'),
    path('main/', views.index, name='main_dashboard'),

    # Department Portals
    path('tms/', views.tms_dashboard, name='tms_dashboard'),
    path('smms/', views.smms_dashboard, name='smms_dashboard'),
    path('trd/', views.trd_dashboard, name='trd_dashboard'),

    # Aliases
    path('engineering/', views.tms_dashboard, name='engineering_dashboard'),
    path('signal/', views.smms_dashboard, name='signal_dashboard'),
    path('traction/', views.trd_dashboard, name='traction_dashboard'),
    path('operations/', views.operations_dashboard, name='operations_dashboard'),
    path('team/<str:team_code>/', views.team_dashboard, name='team_dashboard'),

    # REST APIs: Add Block, Cancel Block, Approval, Reset, Timeline Data, Telemetry
    path('submit-request/', views.submit_request, name='submit_request'),
    path('api/submit-request/', views.submit_request, name='api_submit_request'),
    path('api/cancel-request/<int:req_id>/', views.cancel_request, name='api_cancel_request'),
    path('api/approve-schedule/', views.api_toggle_schedule_approval, name='api_toggle_schedule_approval'),
    path('api/timeline-data/', views.api_timeline_data, name='api_timeline_data'),
    path('api/lagrangian-telemetry/', views.api_lagrangian_telemetry, name='api_lagrangian_telemetry'),
    path('api/optimize/', views.api_lagrangian_telemetry, name='api_optimize'),
    path('api/reset/', views.api_reset_data, name='api_reset_data'),

    # Operational actions
    path('operations/block/<int:block_id>/status/', views.toggle_block_status, name='toggle_block_status'),
    path('notifications/<str:team_code>/read-all/', views.mark_notifications_read, name='mark_notifications_read'),
]
