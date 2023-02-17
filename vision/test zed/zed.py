#!/usr/bin/env python
import os
import subprocess
import json
import inspect
import math
import hashlib

import numpy as np
import torch

import cv2
from cv_bridge import CvBridge
import PIL

import rospy as ros
from copy import deepcopy
from sensor_msgs.msg import Image, PointCloud2
from sensor_msgs import point_cloud2
from std_msgs.msg import String

import orientation

DATASET_PATH = ""

def send_objects(objects: dict, name: str = "detected_objects") -> None:
    """
    Sends detected objects. 
    Once the message is received, you can convert it to a json object by using json.loads(...)

    Input parameters:
    - ``objects`` dict of the detected objects
    - ``name`` (optional, ``default = detected_objects``) specify where the message can be retrieved
    """

    pub = ros.Publisher(name, String, queue_size=1)

    msg = String()
    msg.data = json.dumps(objects)

    pub.publish(msg)

def print_objects(objects: dict) -> None:
    """
    Prints detected objects. 
    This feature is intended for debugging purposes and will only work if the script is executed as the main program.

    Input parameters:
    - ``objects`` dict of the detected objects
    """

    if __name__ != '__main__':
        return
    
    subprocess.run("cls" if os.name == "nt" else "clear", shell=True)

    for obj in objects:
        print(f"{obj['label_name']} {obj['score']}%")

def detect_objects(img: np.ndarray, threshold: float = 0.8, render: bool = False, yolo_path: str = "./yolov5", model_path: str = "best_v6.pt") -> tuple:
    """
    Detects objects inside a given image.

    Input parameters:
    - ``img`` the array of the image to be processed
    - ``threshold`` (optional, ``default = 0.8 -> 80%``) specify the minimum score that a detected object must have in order to be considered "valid"
    - ``render`` (optional, ``default = False``) specifies if the function has to return the image with the detected objects
    - ``yolo_path`` (optional, ``default = "./yolov5"``) yolo folder path
    - ``model_path`` (optional, ``default = "best_v6.pt") model pt file path

    Output parameters (``tuple``):
    - ``result_map`` (``dict``):
        - A dict of the detected objects with their characteristics
    - ``frame`` (``None | np.ndarray``):
        - if ``render = True`` it contains the frame with the bounding boxes of the detected objects, otherwise it will be ``None``
    """
    # check if the threshold is valid
    assert threshold > 0 and threshold <= 1, f"{threshold} is not a valid threshold."

    # get current function as object
    function = eval(inspect.stack()[0][3])

    # in order to reduce the prediction time, the model is loaded once and then saved in .model variable
    try:
        # check if the model has already been loaded
        function.model
    except:
        # load the model (only once)
        function.model = torch.hub.load(yolo_path, 'custom', path=model_path, source='local')

    # process input image
    result = function.model([img], size = 640)

    # keep valid prediction(s) (e.g. score >= threshold)
    thresholded_result = torch.Tensor([item.tolist() for item in result.xyxy[0] if item[-2] >= threshold])
    result.xyxy[0] = thresholded_result
    
    # create objects list
    objects = []

    for item in result.xyxy[0]:

        item = item.tolist()

        box = item[0:4]
        box = [int(b) for b in box]
        box = [(box[0],box[1]),(box[0],box[3]),(box[2],box[1]),(box[2],box[3])]

        center = (int((box[0][0] + box[2][0])/2), int((box[0][1] + box[1][1])/2))

        robot_world_frame_center = convert_to_robot_world_frame(center)
        gazebo_world_frame_center = convert_to_gazebo_world_frame(center)

        objects.append({
            "label_name": result.names[item[5]],
            "label_index": item[5],
            "score": round(item[4]*100,2),
            "box": box,
            "image_center": center,
            "robot_world_frame_center": robot_world_frame_center,
            "gazebo_world_frame_center": gazebo_world_frame_center,
            "box_robot_world_frame": box_robot_world_frame(box),
            "box_gazebo_world_frame": box_gazebo_world_frame(box)
        })

    # if specified, render the result image
    frame = None

    if render:
        result.render()
        frame = result.ims[0]

    return objects, frame

def extract_table(img: np.ndarray, resolution: tuple = (1920,1080), table: list = [[563,274],[452,602],[1026,601],[798,274]]) -> np.ndarray:
    """
    Extracts the table from a given image. In other words, it makes all the image black except for the table

    Input parameters:
    - ``img`` the array of the image to be processed
    - ``resolution`` (optional, ``default = 1920x1080``) resolution of the zed camera
    - ``table`` (optional, ``default = [[563,274],[798,274],[452,602],[1026,601]]``) is a list of the 4 vertices (x,y) of the table

    Output parameter (``np.ndarray``):
    - ``img`` (``np.ndarray``):
        - the processed image
    """

    _table = deepcopy(table)

    if table == [[563,274],[452,602],[1026,601],[798,274]]:
        for coordinate in _table:
            coordinate[0] = int(coordinate[0] * resolution[0] / 1280)
            coordinate[1] = int(coordinate[1] * resolution[1] / 720)

    # create the mask
    mask = np.array(_table, dtype = np.int32)

    # create a black background so, all the image except the table will be black
    background = np.zeros((img.shape[0], img.shape[1]), np.int8)

    # fill the mask with the black background
    cv2.fillPoly(background, [mask],255)

    mask_background = cv2.inRange(background, 1, 255)

    # apply the mask
    img = cv2.bitwise_and(img, img, mask = mask_background)

    return img

def convert_to_robot_world_frame(point: tuple, precision: int = 2) -> tuple:
    """
    Converts 2D coordinates into robot world frame coordinates

    Input parameters:
    - ``point`` 2D point
    - ``precision`` (optional, ``default = 2``) number of decimal digits for the returned coordinates

    Output parameters:
    ``It returns the tuple of the 3D point``
    """

    point_cloud2_msg = ros.wait_for_message("/ur5/zed_node/point_cloud/cloud_registered", PointCloud2)

    # retrieve the center of the detected object
    zed_coordinates = list(point)
    zed_coordinates = [int(coordinate) for coordinate in zed_coordinates]

    # get the 3d point (x,y,z)
    points = point_cloud2.read_points(point_cloud2_msg, field_names=['x','y','z'], skip_nans=False, uvs=[zed_coordinates])
    
    for point in points:
        zed_point = point[:3]

    # transform the 3d point coordinates
    w_R_c = np.array([[ 0.     , -0.49948,  0.86632],[-1.     ,  0.     ,  0.     ],[-0.     , -0.86632, -0.49948]])
    x_c = np.array([-0.9 ,  0.24, -0.35])
    transl_offset = np.array([0.01, 0.00, 0.1])
    zed_point = w_R_c.dot(zed_point) + x_c + transl_offset

    return (round(zed_point[0],precision), round(zed_point[1],precision), round(zed_point[2],precision))

def box_robot_world_frame(box: list, precision: int = 2) -> dict:
    """
    Converts 2D coordinates of the object bounding box to robot world frame coordinates 

    Input parameters:
    - ``box`` list of list [[...],[...],] which represents the bounding box of the detected object
    - ``precision`` (optional, ``default = 2``) number of decimal digits for the returned coordinates

    Output parameters:
    ``It returns a dict where each 2D coordinate has been mapped with its 3D coordinate``
    """

    point_cloud2_msg = ros.wait_for_message("/ur5/zed_node/point_cloud/cloud_registered", PointCloud2)

    img_min_x = min(box, key=lambda x:x[0])[0]
    img_max_x = max(box, key=lambda x:x[0])[0]

    img_min_y = min(box, key=lambda x:x[1])[1]
    img_max_y = max(box, key=lambda x:x[1])[1]

    zed_points = []

    for y in range(img_min_y, img_max_y+1):
        for x in range(img_min_x, img_max_x+1):

            points = point_cloud2.read_points(point_cloud2_msg, field_names=['x','y','z'], skip_nans=False, uvs=[[x,y]])
    
            for point in points:
                zed_point = point[:3]

            #point = list(zed_point)
            #point = [p if not math.isnan(p) else -100 for p in point]

            w_R_c = np.array([[ 0.     , -0.49948,  0.86632],[-1.     ,  0.     ,  0.     ],[-0.     , -0.86632, -0.49948]])
            x_c = np.array([-0.9 ,  0.24, -0.35])
            transl_offset = np.array([0.01, 0.00, 0.1])
            zed_point = w_R_c.dot(zed_point) + x_c + transl_offset

            zed_points.append({
                "2D": (x - img_min_x, y - img_min_y),
                "3D": (round(zed_point[0],precision), round(zed_point[1],precision), round(zed_point[2],precision))
            })

    return zed_points

def convert_to_gazebo_world_frame(point: tuple, precision: int = 2) -> tuple:
    """
    Converts 2D coordinates into gazebo world frame coordinates

    Input parameters:
    - ``point`` 2D point
    - ``precision`` (optional, ``default = 2``) number of decimal digits for the returned coordinates

    Output parameters:
    ``It returns the tuple of the 3D point``
    """

    point_cloud2_msg = ros.wait_for_message("/ur5/zed_node/point_cloud/cloud_registered", PointCloud2)

    # retrieve the center of the detected object
    zed_coordinates = list(point)
    zed_coordinates = [int(coordinate) for coordinate in zed_coordinates]

    # get the 3d point (x,y,z)
    points = point_cloud2.read_points(point_cloud2_msg, field_names=['x','y','z'], skip_nans=False, uvs=[zed_coordinates])
    
    for point in points:
        zed_point = point[:3]

    Ry = np.matrix([[ 0.     , -0.49948,  0.86632],[-1.     ,  0.     ,  0.     ],[-0.     , -0.86632, -0.49948]])
    pos_zed = np.array([-0.9 ,  0.24, -0.35])
    pos_base_link = np.array([0.5,0.35,1.75])
    
    data_world = Ry.dot(zed_point) + pos_zed + pos_base_link
    data_world = np.array(data_world)

    data_world = tuple(data_world.tolist()[0])

    return (round(data_world[0],precision), round(data_world[1],precision), round(data_world[2],precision))

def box_gazebo_world_frame(box: list, precision: int = 2) -> dict:
    """
    Converts 2D coordinates of the object bounding box to gazebo world frame coordinates 

    Input parameters:
    - ``box`` list of list [[...],[...],] which represents the bounding box of the detected object
    - ``precision`` (optional, ``default = 2``) number of decimal digits for the returned coordinates

    Output parameters:
    ``It returns a dict where each 2D coordinate has been mapped with its 3D coordinate``
    """

    point_cloud2_msg = ros.wait_for_message("/ur5/zed_node/point_cloud/cloud_registered", PointCloud2)
    
    img_min_x = min(box, key=lambda x:x[0])[0]
    img_max_x = max(box, key=lambda x:x[0])[0]

    img_min_y = min(box, key=lambda x:x[1])[1]
    img_max_y = max(box, key=lambda x:x[1])[1]

    zed_points = []

    for y in range(img_min_y, img_max_y+1):
        for x in range(img_min_x, img_max_x+1):

            points = point_cloud2.read_points(point_cloud2_msg, field_names=['x','y','z'], skip_nans=False, uvs=[[x,y]])
    
            for point in points:
                zed_point = point[:3]

            point = list(zed_point)
            point = [p if not math.isnan(p) else -100 for p in point]

            Ry = np.matrix([[ 0.     , -0.49948,  0.86632],[-1.     ,  0.     ,  0.     ],[-0.     , -0.86632, -0.49948]])
            pos_zed = np.array([-0.9 ,  0.24, -0.35])
            pos_base_link = np.array([0.5,0.35,1.75])
            
            data_world = Ry.dot(zed_point) + pos_zed + pos_base_link
            data_world = np.array(data_world)

            data_world = tuple(data_world.tolist()[0])
            data_world = (round(data_world[0],precision), round(data_world[1],precision), round(data_world[2],precision))

            zed_points.append({
                "2D": (x - img_min_x, y - img_min_y),
                "3D": data_world
            })

    return zed_points

def background_detection(msg: Image) -> None:
    """
    Receives and elaborates zed images in background (no display)

    Example:
        >>> import rospy as ros
        >>> from sensor_msgs.msg import Image
        >>> if __name__ == "__main__":
        >>>     ros.init_node('mynode', anonymous=True)
        >>>     ros.Subscriber("/ur5/zed_node/left_raw/image_raw_color", Image, callback = background_detection, queue_size=1)
        >>>     loop_rate = ros.Rate(1.)
        >>>     while True:
        >>>         loop_rate.sleep()
    """
    global DATASET_PATH

    # convert received image (bgr8 format) to a cv2 image
    img = CvBridge().imgmsg_to_cv2(msg, "bgr8")

    # extract table from image
    img = extract_table(img = img)

    # detect objects
    objects, frame = detect_objects(img = img)

    # analyze objects
    objects = orientation.process_frame(objects=objects,frame=img,dataset_path=DATASET_PATH)

    # send detected objects
    send_objects(objects = objects)
    
    # print detected objects
    print_objects()

def live_detection(msg: Image) -> None:
    """
    Displays detected objects

    Example:
        >>> import rospy as ros
        >>> from sensor_msgs.msg import Image
        >>> if __name__ == "__main__":
        >>>     ros.init_node('mynode', anonymous=True)
        >>>     ros.Subscriber("/ur5/zed_node/left_raw/image_raw_color", Image, callback = live_detection, queue_size=1)
        >>>     loop_rate = ros.Rate(1.)
        >>>     while True:
        >>>         loop_rate.sleep()
    """
    global DATASET_PATH

    # get current function as object
    function = eval(inspect.stack()[0][3])

    # init function variables
    try:
        function.prev_hash
    except:
        function.prev_hash = None
    
    current_hash = str(hashlib.sha256(msg.data).hexdigest())

    # check if the previous frame is equal to the new one
    # in this case don't process the new frame
    if current_hash == function.prev_hash:
        return
    
    # save the hash of the new frame
    function.prev_hash = current_hash

    # convert received image (bgr8 format) to a cv2 image
    img = CvBridge().imgmsg_to_cv2(msg, "bgr8")

    # extract table from image
    img = extract_table(img = img)

    # detect objects
    objects, frame = detect_objects(img = img, render = True)

    # analyze objects
    objects = orientation.process_frame(objects=objects,frame=img,dataset_path=DATASET_PATH)

    # send detected objects
    send_objects(objects = objects)

    #PIL.Image.fromarray(objects).save("frame.png")

    # print detected objects
    print_objects()

    # display processed image
    cv2.imshow("Live detection",frame)

    # q for exit
    if cv2.waitKey(1) == ord('q'):
        exit(0)

if __name__ == '__main__':

    ros.init_node("vision", anonymous=True)

    #ros.Subscriber("/ur5/zed_node/left_raw/image_raw_color", Image, callback = background_detection, queue_size=1)
    ros.Subscriber("/ur5/zed_node/left_raw/image_raw_color", Image, callback = live_detection, queue_size=1)

    loop_rate = ros.Rate(1.)

    while True:
        loop_rate.sleep()
        pass