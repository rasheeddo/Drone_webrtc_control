
import rospy
from dronekit import connect, VehicleMode, Command
import time
import numpy as np
import json
import subprocess
import os
import argparse
from datetime import datetime
from std_msgs.msg import String
from webrtc_telemetry.msg import ConsoleCmd
import boto3
from botocore.exceptions import ClientError
from botocore.exceptions import NoCredentialsError


parser = argparse.ArgumentParser(description='Ardupilot_data_publisher_node')
parser.add_argument('--console_port',
				help="This is a second port generated by socat.sh")
parser.add_argument('--udp',
				help="IP of Cubepilot with port, e.i. localhost:14550, 192.168.8.126:14550")
parser.add_argument('--serial',
				help="a serial port of Cubepilot, /dev/ttyUSB0:921600")
parser.add_argument('--ns',
				help="a robot namespace for ros")
parser.add_argument('--id',
				help="ID of vehicle")
parser.add_argument('--s3',
				help="S3 bucket which stored a mission file")

args = parser.parse_args()
CONSOLE_PORT = args.console_port
UDP = args.udp
UART = args.serial
NS = args.ns
S3_bucket = args.s3

if CONSOLE_PORT is None:
	print("Error: please specify second port of socat generated")
	quit()

if UART is not None:
	connection_str = UART.split(":")
	uart_conn = True
else:
	uart_conn = False

if UDP is not None:
	connection_str = UDP.split(":")
	udp_conn = True
else:
	udp_conn = False

if (uart_conn == False) and (udp_conn == False):
	prnt("Please specify --udp or --serial connection string")
	quit()

if NS is not None:
	print("Use namespace as {:}".format(NS))
else:
	print("No namespace, using default")

if args.id is None:
	print("Use id 1 as default")
	_id = 1
else:
	_id = int(args.id)

if S3_bucket is None:
	# rospack = rospkg.RosPack()
	# webrtc_telem_ros_path = rospack.get_path("webrtc_telemetry")
	# mission_path = os.path.join(webrtc_telem_ros_path, "waypoints")
	mission_path = os.getcwd()
	print("No S3 bucket specified, using {:} as missions local directory".format(mission_path))
	use_local_file = True
else:
	s3 = boto3.client('s3')
	try:
		result = s3.get_bucket_acl(Bucket='ginzafarm-drone-missions')
	except ClientError as e:
		print("Client Error: {:}".format(e))
		quit()
	except NoCredentialsError as e:
		print("Credentials not available")
		print(e)
		quit()

	print("Using S3 bucket: {:}".format(S3_bucket))
	use_local_file = False


###############################################
################# Init/Declare ################
###############################################


rospy.init_node("webrtc_telemetry_node", anonymous=True)

if connection_str[0].startswith('/dev/'):
	device = connection_str[0]
	baudrate = int(connection_str[1])
	vehicle = connect(device, baud=baudrate)
else:
	vehicle = connect(connection_str[0]+":"+connection_str[1])

roll = 0.0
pitch = 0.0
yaw = 0.0
lat = 0.0
lon = 0.0
fix_type = 0
alt_rel = 0.0
alt = 0.0
home_alt = 0.0
airspeed = 0.0
groundspeed = 0.0
mode = "HOLD"
nextwp = 0
travelled = 0.0
tohome = 0.0
home_lat = 0.0
home_lon = 0.0
prev_lat = 0.0
prev_lon = 0.0
volt = 0.0
current = 0.0
# distToNextWp = 0.0
wp_speed = 10.0
got_mission = False
telem_dict = {
				"telemetry": {
						"pos": {
								"lat": lat, "lon": lon, "alt": alt_rel},
						"att": {
								"roll": roll, "pitch": pitch, "yaw": yaw},
						"dist": {"travelled": travelled, "toHome": tohome, "toNextWp": 0.0},
						"speed": {"air": airspeed, "ground": groundspeed},
						"nav": {"nextWp": 0, "eta":[]},
						"mode": "MAN",
						"gps": "",
						"batt": {"volt": volt, "current": current}
						},
				"id": _id	
			}

proj_path = os.getcwd()
file_path = os.path.join(proj_path, "console_telemetry.txt")

############################################
################# Functions ################
############################################
def console_cmd_callback(msg):
	global mode
	global target_lat_list, target_lon_list, total_points, got_mission
	global S3_bucket, s3, use_local_file, mission_path
	# print(msg.data)
	# s3 = boto3.client('s3')
	# s3.download_file("ginzafarm-drone-missions", msg.data, msg.data)
	if len(msg.mission.data) > 0:
		if ".txt" in msg.mission.data:
			mission_name = msg.mission.data
		else:
			mission_name = msg.mission.data + ".txt"

		if not use_local_file:
			try:
				s3.download_file(S3_bucket, mission_name, mission_name)
			except FileNotFoundError as e:
				print(e)
				print("S3 Download error, {:} file not found".format(mission_name))
			else:
				upload_mission_from_file(mission_name)
		else:
			mission_file_path = os.path.join(mission_path, mission_name)
			if os.path.exists(mission_file_path):
				upload_mission_from_file(mission_file_path)
			else:
				print("There is no {:}".format(mission_file_path))

		target_lat_list, target_lon_list, total_points = getMission()
		got_mission = True

	else:
		print("No request mission coming on topic")

	if len(msg.mode.data) > 0:
		vehicle.mode = msg.mode.data
		print("change flight mode to {:}".format(msg.mode.data))
	else:
		print("No request mode")



def get_distance(lat1, lon1, lat2, lon2):

	R = 6371.0*1000.0
	lat_start = np.radians(lat1)
	lon_start = np.radians(lon1)
	lat_end = np.radians(lat2)
	lon_end = np.radians(lon2)
	dLat = lat_end - lat_start
	dLon = lon_end - lon_start

	a = np.sin(dLat/2.0)*np.sin(dLat/2.0) + np.cos(lat_start)*np.cos(lat_end)*np.sin(dLon/2.0)*np.sin(dLon/2.0)
	c = 2.0*np.arctan2(np.sqrt(a),np.sqrt(1-a))

	d = c*R

	return d

# Callback to print the location in global frame
def location_callback(self, attr_name, value):
	global lat, lon, alt_rel, telem_dict, prev_lat, prev_lon, travelled
	global got_mission, target_lat_list, target_lon_list, total_points, nextwp

	# global cur_lat, cur_lon
	lat = value.global_frame.lat
	lon = value.global_frame.lon

	alt_rel = value.global_relative_frame.alt

	telem_dict["telemetry"]["pos"]["lat"] = lat
	telem_dict["telemetry"]["pos"]["lon"] = lon
	telem_dict["telemetry"]["pos"]["alt"] = round(alt_rel,2)

	_mode = telem_dict["telemetry"]["mode"]

	if (lat != 0.0) and (lon != 0.0) and (prev_lat != 0.0) and (prev_lon != 0.0) and ((_mode == "AUTO") or (_mode == "GUIDED")):
		travelled += get_distance(prev_lat, prev_lon, lat, lon)
		telem_dict["telemetry"]["dist"]["travelled"] = round(travelled, 2)

		if got_mission and (nextwp != 0):
			telem_dict["telemetry"]["dist"]["toNextWp"] = round(get_distance(lat, lon, target_lat_list[nextwp-1], target_lon_list[nextwp-1]),2)

	prev_lat = lat
	prev_lon = lon
		
def attitude_callback(self, attr_name, value):
	global roll, pitch, yaw, telem_dict
	# global cur_yaw
	roll = np.degrees(value.roll)
	pitch = np.degrees(value.pitch)
	yaw = np.degrees(value.yaw)

	telem_dict["telemetry"]["att"]["roll"] = round(roll,2)
	telem_dict["telemetry"]["att"]["pitch"] = round(pitch,2)
	telem_dict["telemetry"]["att"]["yaw"] = round(yaw,2)

def gps_callback(self, attr_name, value):
	global fix_type, telem_dict
	# global gps_status
	fix_type = value.fix_type

	# 3 = 3DFix
	# 4 = 3DGPS
	# 5 = rtkFloat
	# 6 = rtkFixed
	## range is -pi to pi, 0 is north
	if fix_type < 3:
		telem_dict["telemetry"]["gps"]= ""
	elif fix_type == 3:
		telem_dict["telemetry"]["gps"]= "3D"
	elif fix_type == 4:
		telem_dict["telemetry"]["gps"]= "DGPS"
	elif fix_type == 5:
		telem_dict["telemetry"]["gps"]= "RTKFLT"
	elif fix_type == 6:
		telem_dict["telemetry"]["gps"]= "RTKFXD"

def groundspeed_callback(self, attr_name, value):
	global groundspeed, telem_dict
	groundspeed = value
	telem_dict["telemetry"]["speed"]["ground"] = round(groundspeed,2)

def airspeed_callback(self, attr_name, value):
	global airspeed, telem_dict
	airspeed = value
	telem_dict["telemetry"]["speed"]["air"] = round(airspeed,2)

def mode_callback(self, attr_name, value):
	global mode, telem_dict
	mode = value.name
	# if (mode == "MANUAL") or (mode == "STABILIZE") or (mode == "ALT_HOLD") or (mode == "ACRO"):
	# 	telem_dict["telemetry"]["mode"] = "MAN"
	# elif (mode == "AUTO") or (mode == "LOITER") or (mode == "GUIDED") or (mode == "RTL") or (mode == "LAND"):
	# 	telem_dict["telemetry"]["mode"] = "AUTO"
	telem_dict["telemetry"]["mode"] = mode

def batt_callback(self, attr_name, value):
	global volt, telem_dict, current
	volt = value.voltage
	current = value.current
	telem_dict["telemetry"]["batt"]["volt"] = round(volt,2)
	telem_dict["telemetry"]["batt"]["current"] = round(current,2)

def wpnav_speed_callback(self, attr_name, value):
	global wp_speed

	wp_speed = value
	# print("got wp_speed", wp_speed)

def getMission():

	global cmds, got_mission

	cmds.download()
	cmds.wait_ready()

	total_wps = vehicle.commands.count

	lat_list = np.array([])
	lon_list = np.array([])
	alt_list = np.array([])

	for i in range(total_wps):
		wp = vehicle.commands[i]
		lat_list = np.append(lat_list, wp.x)
		lon_list = np.append(lon_list, wp.y)
		alt_list = np.append(alt_list, wp.z)

		got_mission = True


	print("total_wps: ", total_wps)

	print("lat", lat_list)
	print("lon", lon_list)

	return lat_list, lon_list, total_wps

def readmission(aFileName):
	global cmds
	"""
	Load a mission from a file into a list. The mission definition is in the Waypoint file
	format (http://qgroundcontrol.org/mavlink/waypoint_protocol#waypoint_file_format).

	This function is used by upload_mission().
	"""
	print("\nReading mission from file: %s" % aFileName)
	# cmds = vehicle.commands
	missionlist=[]
	with open(aFileName) as f:
		for i, line in enumerate(f):
			if i==0:
				if not line.startswith('QGC WPL 110'):
					raise Exception('File is not supported WP version')
			else:
				linearray=line.split('\t')
				ln_index=int(linearray[0])
				ln_currentwp=int(linearray[1])
				ln_frame=int(linearray[2])
				ln_command=int(linearray[3])
				ln_param1=float(linearray[4])
				ln_param2=float(linearray[5])
				ln_param3=float(linearray[6])
				ln_param4=float(linearray[7])
				ln_param5=float(linearray[8])
				ln_param6=float(linearray[9])
				ln_param7=float(linearray[10])
				ln_autocontinue=int(linearray[11].strip())
				cmd = Command( 0, 0, 0, ln_frame, ln_command, ln_currentwp, ln_autocontinue, ln_param1, ln_param2, ln_param3, ln_param4, ln_param5, ln_param6, ln_param7)
				
				## We ignore index 0 of waypoints which is home position
				if ln_index != 0:
					missionlist.append(cmd)

	f.close()

	return missionlist

def upload_mission_from_file(file_path):
	global cmds
	"""
	Upload a mission from a file. 
	"""
	# path = os.getcwd()
	# file_path = os.path.join(path, "MISSION.txt")
	if os.path.exists(file_path):
		#Read mission from file
		missionlist = readmission(file_path)
		# print("missionlist", missionlist)
		print("\nUpload mission from a file: %s" % file_path)
		#Clear existing mission from vehicle
		print('Clear old mission...')
		# cmds = vehicle.commands
		cmds.clear()
		#Add new mission to vehicle
		# print("missionlist", missionlist)
		for command in missionlist:
			# print("command", command)
			cmds.add(command)

		## sometime there is error of Nonetype not iterable
		## but the mission already uploaded, so just pass it
		try:
			vehicle.commands.upload()
			print('MISSION Uploaded')
		except Exception as e:
			print("Exception!!! error as -> %s, but don't care..." %e)
			print('MISSION Uploaded with some exception')
			pass

		return True
	else:
		print("ERROR missing MISSION.txt file")
		return False

def calculate_ETA(nextwp, cur_lat, cur_lon):

	global target_lat_list, target_lon_list, total_points
	global groundspeed, wp_speed

	ETA_list = []
	linuxTime_list = []

	## nextwp in Ardupilot never be 0, always start from 1
	for i in range(total_points):

		################################
		## Points that already passed ##
		################################
		if i < (nextwp-1):

			ETA_passed = "Passed"
			ETA_list.append(ETA_passed)
			linuxTime_list.append(time.time())

		#############################
		## Current point to nextwp ##
		#############################
		elif i == (nextwp-1):
			
			dist_to_next = get_distance(cur_lat, cur_lon, target_lat_list[nextwp-1], target_lon_list[nextwp-1])

			if groundspeed == 0.0:
				vel = wp_speed/100.0
			else:
				vel = groundspeed

			elaspedTime_to_next_in_sec = dist_to_next/vel
			linuxTime_to_next = time.time() + elaspedTime_to_next_in_sec
			human_time_to_next = datetime.fromtimestamp(linuxTime_to_next)
			ETA_next = human_time_to_next.strftime("%H:%M:%S")
			ETA_list.append(ETA_next)
			linuxTime_list.append(linuxTime_to_next)

		##########################
		## Points in the future ##
		##########################
		elif i > (nextwp-1):
			dist_to_next = get_distance(target_lat_list[i-1], target_lon_list[i-1], target_lat_list[i], target_lon_list[i])
			vel = wp_speed/100.0

			elaspedTime_to_next_in_sec = dist_to_next/vel
			linuxTime_to_future = linuxTime_list[i-1] + elaspedTime_to_next_in_sec
			human_time_to_future = datetime.fromtimestamp(linuxTime_to_future)
			ETA_future = human_time_to_future.strftime("%H:%M:%S")
			ETA_list.append(ETA_future)
			linuxTime_list.append(linuxTime_to_future)

		if (groundspeed < 0.1) and (nextwp == total_points):
			ETA_list = ["Passed"]*total_points

	return ETA_list



####################################################
################# Dronekit Listener ################
####################################################

vehicle.add_attribute_listener('location', location_callback)
vehicle.add_attribute_listener('attitude', attitude_callback)
vehicle.add_attribute_listener('gps_0', gps_callback)
vehicle.add_attribute_listener('groundspeed', groundspeed_callback)
vehicle.add_attribute_listener('airspeed', airspeed_callback)
vehicle.add_attribute_listener('mode', mode_callback)
vehicle.add_attribute_listener('battery', batt_callback)
# vehicle.add_attribute_listener('commands', nextwp_callback)
vehicle.parameters.add_attribute_listener("WPNAV_SPEED", wpnav_speed_callback)


#################################################
################# ROS Subscriber ################
#################################################

if NS is None:
	console_cmd_topic = "/console_cmd"
else:
	if NS.startswith("/"):
		console_cmd_topic = NS + "/console_cmd"
	else:
		console_cmd_topic = "/" + NS + "/console_cmd"

rospy.Subscriber(console_cmd_topic, ConsoleCmd, console_cmd_callback)


#######################################
################# Loop ################
#######################################
global cmds
cmds = vehicle.commands

global target_lat_list, target_lon_list, total_points
target_lat_list, target_lon_list, total_points = getMission()

rate = rospy.Rate(10)
ETA_list = []

while not rospy.is_shutdown():

	nextwp = cmds.next
	telem_dict["telemetry"]["nav"]["nextWp"] = nextwp

	if (mode == "AUTO") and (nextwp != 0):
		ETA_list = calculate_ETA(nextwp, lat, lon)
		print(ETA_list)
	else:
		ETA_list = []

	telem_dict["telemetry"]["nav"]["eta"] = ETA_list

	json_data = json.dumps(telem_dict)

	home = vehicle.home_location
	if home is not None:
		home_lat = home.lat
		home_lon = home.lon

	if (home_lat != 0.0) and (home_lon != 0.0):
		tohome = get_distance(home_lat, home_lon, lat, lon)
		telem_dict["telemetry"]["dist"]["toHome"] = round(tohome, 2)


	print("{:} | next: {:d} | r: {:.2f} | p: {:.2f} | y: {:.2f} | lat: {:.5f} | lon: {:.5f} | altRel: {:.2f} | fix: {:d} | ASPD: {:.2f} | GSPD: {:.2f} | hLat: {:.5f} | hLon: {:.5f} | tohome: {:.2f} | trav: {:.2f} | volt: {:.2f}".format(\
		mode, nextwp, roll, pitch, yaw, lat, lon, alt_rel, fix_type, airspeed, groundspeed, home_lat, home_lon, tohome, travelled, volt))

	file = open(file_path, "w+") 
	file.write(json_data)
	cmd1 = 'echo $(cat console_telemetry.txt) > {:s}'.format(CONSOLE_PORT)
	subprocess.call(cmd1, shell=True)				# for python2
	# subprocess.run(cmd1, shell=True, check=True)	# for python3

	# with open("lat_lon.txt", "a") as log_file:
	# 	log_file.write(str(lat))
	# 	log_file.write(",")
	# 	log_file.write(str(lon))
	# 	log_file.write("\n")


	rate.sleep()