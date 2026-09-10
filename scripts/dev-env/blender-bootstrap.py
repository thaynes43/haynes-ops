"""Register the image's pinned addon in a software-rendered Blender desktop."""
import importlib
import os
import sys

import addon_utils
import bpy
from bpy.app.handlers import persistent

# Use the module's real filename so importlib.reload() can discover its spec.
# Blender 4.5 enable() compares __time__ with the source mtime before register().
# Seed that timestamp for this preloaded, immutable module so enable() retains
# the LocalServer override installed below instead of reloading it away.
NAME = 'addon'
sys.path.insert(0, '/opt/dev-env/blender-mcp')
addon = importlib.import_module(NAME)
addon.__time__ = os.path.getmtime(addon.__file__)



class LocalServer(addon.BlenderMCPServer):
    def __init__(self, host='127.0.0.1', port=9876):
        # Both register() and the UI operator must bind only this exact address.
        super().__init__(host='127.0.0.1', port=9876)

    def execute_command(self, command):
        if command.get('type') == 'authoring_health':
            prefs = addon.get_blendermcp_addon_preferences()
            return {'status': 'success', 'result': {
                'token': os.environ['BLENDER_AUTHORING_TOKEN'],
                'protocol': addon.ADDON_PROTOCOL_VERSION,
                'telemetry': bool(prefs.telemetry_consent) if prefs else None,
            }}
        return super().execute_command(command)


addon.BlenderMCPServer = LocalServer
enabled = addon_utils.enable(NAME, default_set=True, persistent=True)
if enabled is not addon or addon.BlenderMCPServer is not LocalServer or NAME not in bpy.context.preferences.addons:
    raise RuntimeError('Pinned Blender MCP addon did not enable')
prefs = bpy.context.preferences.addons[NAME].preferences
prefs.telemetry_consent = False
addon.sync_edit_capture_handlers()
bpy.context.preferences.system.use_online_access = False


@persistent
def configure_scene(_unused=None):
    for scene in bpy.data.scenes:
        scene.blendermcp_port = 9876
        scene.blendermcp_auto_start_server = True
        for service in ('polyhaven', 'hyper3d', 'sketchfab', 'polypizza', 'hunyuan3d'):
            setattr(scene, f'blendermcp_use_{service}', False)
    server = getattr(bpy.types, 'blendermcp_server', None)
    if server is None:
        server = bpy.types.blendermcp_server = LocalServer()
    if not server.running:
        server.start()
    # Upstream's timer is persistent; restore defensively after loading .blend files.
    if not bpy.app.timers.is_registered(server._drain_command_queue):
        bpy.app.timers.register(server._drain_command_queue, persistent=True)
    for scene in bpy.data.scenes:
        scene.blendermcp_server_running = server.running
    addon.sync_edit_capture_handlers()


bpy.app.handlers.load_post.append(configure_scene)
configure_scene()
bpy.ops.wm.save_userpref()
print('Local Blender authoring bootstrap ready; external services and telemetry disabled')
