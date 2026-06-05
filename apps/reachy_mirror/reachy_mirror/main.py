import threading
import time
import math
from typing import Optional

import cv2
import base64
import numpy as np
import mediapipe as mp
import math
from pydantic import BaseModel
from reachy_mini.utils import create_head_pose
from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini.motion.recorded_move import RecordedMoves
from fastapi.responses import StreamingResponse

mpFaceMesh = mp.solutions.face_mesh
faceMesh = mpFaceMesh.FaceMesh(static_image_mode=True, max_num_faces=1, min_detection_confidence=0.5, min_tracking_confidence=0.5)
mpHands = mp.solutions.hands
hands = mpHands.Hands()
mpDraw = mp.solutions.drawing_utils
mpDrawingStyles = mp.solutions.drawing_styles

VERTICAL_PITCH_CORRECTION=-5

class ReachyMirror(ReachyMiniApp):
  custom_app_url: str | None = "http://0.0.0.0:7860"
  request_media_backend: str | None = None
    
  def __init__(self):
    super().__init__()
    self.appReady: bool = False
    self.isProcessing = False
    self.width: int = 640
    self.height: int = 480
    self.lastFrame: Optional[np.ndarray] = None
    self.headAngles = [0,0,0]
    self.antennaAngles = [0,0]
    self.frameRateDown = 30
    self.motionReduction = 60
    self.showProcessing = True
    self.isMirror = True
    self.frameCount = 0
    self.frameCountTotal = 0
    self.downValue = 0
    self.downValueAvg = 0
    self._setupEndpoints()

  def _setupEndpoints(self):
    """Set up FastAPI endpoints for the web UI"""
    @self.settings_app.get("/ready")
    async def ready():
      return {"ready": self.appReady}

    @self.settings_app.get("/webcam_feed")
    def webcam_feed():
      return StreamingResponse(
        self._frameGenerator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
      )
    
    class UIState(BaseModel):
      frameRateDown: int | None = None
      motionReduction: int | None = None
      showProcessing: bool | None = None
      isMirror: bool | None = None

    @self.settings_app.post("/settings")
    async def update_settings(state: UIState):
      if state.motionReduction is not None:
        self.motionReduction = state.motionReduction
      if state.frameRateDown is not None:
        self.frameRateDown = state.frameRateDown
      if state.showProcessing is not None:
        self.showProcessing = state.showProcessing
      if state.isMirror is not None:
        self.isMirror = state.isMirror
    
    class FrameData(BaseModel):
      image: str | None = None

    @self.settings_app.post("/process_frame")
    def process_frame(data: FrameData):
      if self.isProcessing is False:
        self.isProcessing = True
        try:
          # Remove data:image/jpeg;base64, prefix
          image_data = data.image.split(',')[1]
          image_bytes = base64.b64decode(image_data)

          # Convert to numpy array
          nparr = np.frombuffer(image_bytes, np.uint8)
          frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
          
          if frame is None:
            self.isProcessing = False
            return {'error': 'Could not decode frame'}
          
          # Process frame and get image with landmarks
          image = self._drawLandmarksToFrame(frame)
          
          if image is not None and self.showProcessing is True:
            ret, buffer = cv2.imencode('.jpg', image)
            if ret is None:
              return {'error': 'Could not convert frame'}
            processed_frame = base64.b64encode(buffer).decode('utf-8')
            self.isProcessing = False
            return {
              'image': f'data:image/jpeg;base64,{processed_frame}',
              'head': [int(self.headAngles[0]), int(self.headAngles[1]), int(self.headAngles[2])],
              'hands': [int(180*self.antennaAngles[0]/math.pi), int(180*self.antennaAngles[1]/math.pi)],
              'downValue': self.downValue,
              'downValueAvg': self.downValueAvg
            }

          self.isProcessing = False
          return {}
        except Exception as e:
          self.isProcessing = False
          return {'error': str(e)}
      return {}
    
  def _frameGenerator(self):
    """Generate MJPEG frames for streaming"""
    while True:
      if self.lastFrame is None:
        time.sleep(0.05)
        continue
      ret, jpeg = cv2.imencode(".jpg", self.lastFrame)
      if ret:
        yield (
          b"--frame\r\n"
          b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
        )
      time.sleep(0.05)
  
  # inspired by https://github.com/shenasa-ai/head-pose-estimation/blob/main/estimator.py
  def _rotationMatrixToAngles(self, rotationMatrix):
    """
    Calculate Euler angles from rotation matrix.
    :param rotationMatrix: A 3*3 matrix with the following structure
    [Cosz*Cosy  Cosz*Siny*Sinx - Sinz*Cosx  Cosz*Siny*Cosx + Sinz*Sinx]
    [Sinz*Cosy  Sinz*Siny*Sinx + Sinz*Cosx  Sinz*Siny*Cosx - Cosz*Sinx]
    [  -Siny             CosySinx                   Cosy*Cosx         ]
    :return: Image with landmarks for head and hands
    """
    x = math.atan2(rotationMatrix[2, 1], rotationMatrix[2, 2])
    y = math.atan2(-rotationMatrix[2, 0], math.sqrt(rotationMatrix[0, 0] ** 2 + rotationMatrix[1, 0] ** 2))
    z = math.atan2(rotationMatrix[1, 0], rotationMatrix[0, 0])
    return np.array([x, y, z]) * 180. / math.pi

  def _drawLandmarksToFrame(self, image):
    if image is None:
      return None
    
    if self.isMirror:
      image = cv2.flip(image, 1)
    h, w, _ = image.shape
    faceCoordinationInImage = []
    imgRGB = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    resultsHand = hands.process(imgRGB)
    handsAngle = []
    handsType=[]
    if resultsHand.multi_hand_landmarks:
      for hand in resultsHand.multi_handedness:
        handType=hand.classification[0].label
        handsType.append(handType)
      for handLms in resultsHand.multi_hand_landmarks:
        mpDraw.draw_landmarks(image, handLms, mpHands.HAND_CONNECTIONS, None)
        base = None
        tip = None
        for id, lm in enumerate(handLms.landmark):
          h, w, c = image.shape
          cx, cy = int(lm.x * w), int(lm.y * h)
          if id == 0:
            base = [cx, cy]
            cv2.circle(image, (cx, cy), 8, (255, 196, 69), cv2.FILLED)
          elif id == 8:
            cv2.circle(image, (cx, cy), 8, (255, 196, 69), cv2.FILLED)
            tip = [cx, cy]
          else:
            cv2.circle(image, (cx, cy), 4, (255, 196, 69), cv2.FILLED)
        handsAngle.append(math.atan2(tip[0]-base[0],tip[1]-base[1]))

    angles = [0,0]
    if len(handsAngle) > 0:
      if handsType[0] == 'Left':
        angles[0] = -handsAngle[0]+math.pi
      else:
        angles[1] = -handsAngle[0]-math.pi
    if len(handsAngle) > 1:
      if handsType[1] == 'Left':
        angles[0] = -handsAngle[1]+math.pi
      else:
        angles[1] = -handsAngle[1]-math.pi
    if abs(angles[0]) > math.pi:
      angles[0] = angles[0]-2*math.pi
    if abs(angles[1]) > math.pi:
      angles[1] = angles[1]+2*math.pi
    self.antennaAngles = angles

    resultsHead = faceMesh.process(imgRGB)
    faceCoordinationInRealWorld = np.array([
        [285, 528, 200],
        [285, 371, 152],
        [197, 574, 128],
        [173, 425, 108],
        [360, 574, 128],
        [391, 425, 108]
    ], dtype=np.float64)
    if resultsHead.multi_face_landmarks:
      for face_landmarks in resultsHead.multi_face_landmarks:
        for idx, lm in enumerate(face_landmarks.landmark):
          h, w, _ = image.shape
          x, y, z = int(lm.x * w), int(lm.y * h), lm.z
          if self.showProcessing is True:
            cv2.circle(image, (x, y), 1, (255, 196, 69), cv2.FILLED)
          if idx in [1, 9, 57, 130, 287, 359]:
            x, y = int(lm.x * w), int(lm.y * h)
            faceCoordinationInImage.append([x, y])

        faceCoordinationInImage = np.array(faceCoordinationInImage, dtype=np.float64)
        focalLength = 1 * w
        camMatrix = np.array([[focalLength, 0, w / 2], [0, focalLength, h / 2], [0, 0, 1]])
        distMatrix = np.zeros((4, 1), dtype=np.float64)
        _, rotationVec, _ = cv2.solvePnP(faceCoordinationInRealWorld, faceCoordinationInImage, camMatrix, distMatrix)
        rotationMatrix, _ = cv2.Rodrigues(rotationVec)
        self.headAngles = self._rotationMatrixToAngles(rotationMatrix)

        return image
    return None

  def run(self, reachy_mini: ReachyMini, stop_event: threading.Event):
    """
    Main loop
    """
    t0Total = time.time()
    t0Last = time.time()
    t0 = time.time()
    frame = None

    # Load recorded moves for sounds
    try:
      self.recorded_moves = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
    except Exception as e:
      print(f"Could not load emotions library: {e}")

    try:
      head_pose = create_head_pose(pitch=VERTICAL_PITCH_CORRECTION)
      reachy_mini.goto_target(head_pose, antennas=[-.5, .5], body_yaw=0.0, duration=1.0)
      reachy_mini.media.play_sound("wake_up.wav")
    except Exception as e:
      print(f"Greeting failed: {e}")
    
    self.appReady = True
    print("🪞 Reachy Mirror is ready !")

    # Main control loop
    while not stop_event.is_set():
      frame = reachy_mini.media.get_frame()

      if frame is None:
        print("Failed to grab frame.")
        continue

      # Resize and flip frame if isMirror
      image = cv2.resize(frame, (640, 480))
      if self.isMirror:
        image = cv2.flip(image, 1)
      self.lastFrame = image

      # Set head pose with motion reduction param
      r=self.motionReduction/100
      if self.isMirror:
        reachy_mini.set_target(
          head=create_head_pose(pitch=-self.headAngles[0]*r+VERTICAL_PITCH_CORRECTION, yaw=self.headAngles[1]*r, roll=-self.headAngles[2]*r),
          antennas=self.antennaAngles
        )
      else:
        reachy_mini.set_target(
          head=create_head_pose(pitch=-self.headAngles[0]*r+VERTICAL_PITCH_CORRECTION, yaw=self.headAngles[1]*r, roll=-self.headAngles[2]*r),
          antennas=[self.antennaAngles[0], self.antennaAngles[1]]
        )

      self.frameCount += 1
      self.frameCountTotal += 1
      now = time.time()
      elapsed = now - t0Last
      sleep_time = max(0, (1.0 / self.frameRateDown) - elapsed)
      t0Last = now

      if now - t0 > 1.0:
        self.downValue = math.floor(self.frameCount/(now - t0)*10)/10
        self.downValueAvg = math.floor(self.frameCountTotal/(now - t0Total)*10)/10
        t0 = now
        self.frameCount = 0

      time.sleep(sleep_time)

if __name__ == "__main__":
  app = ReachyMirror()
  try:
    app.wrapped_run()
  except KeyboardInterrupt:
    app.stop()