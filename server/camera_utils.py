"""
Camera utilities — helper code snippets appended to design scripts
to control the viewport before render capture.
"""


def fit_to_design() -> str:
    """Returns Python code that frames the camera on all geometry with isometric view."""
    return """
# --- Camera setup for render ---
_vp = app.activeViewport
_cam = _vp.camera
_cam.isFitView = True
_cam.isSmoothTransition = False
_cam.viewOrientationType = adsk.core.ViewOrientations.IsoTopRightViewOrientation
_vp.camera = _cam
_vp.fit()
adsk.doEvents()
"""
