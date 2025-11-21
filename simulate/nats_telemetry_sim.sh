#!/bin/bash

# NATS JetStream Telemetry Simulation
# Simulates realistic telemetry data for drone and robot

echo "Starting NATS telemetry simulation..."
echo "Press Ctrl+C to stop"
echo ""

# Counter for message IDs
counter=0

while true; do
  counter=$((counter + 1))
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
  
  # Drone Telemetry (simulating flight patterns)
  drone_lat=$(echo "37.7749 + (s($counter * 0.1) * 0.001)" | bc -l)
  drone_lon=$(echo "-122.4194 + (c($counter * 0.1) * 0.001)" | bc -l)
  drone_alt=$(echo "50 + (s($counter * 0.2) * 10)" | bc -l)
  drone_battery=$(echo "100 - ($counter * 0.05)" | bc -l | awk '{printf "%.2f", ($1 > 0) ? $1 : 0}')
  drone_speed=$(echo "15 + (s($counter * 0.15) * 5)" | bc -l | awk '{printf "%.2f", $1}')
  drone_heading=$(echo "($counter * 2) % 360" | bc)
  
  nats pub constellation.telemetry.drone "{
    \"device_id\": \"drone-001\",
    \"device_type\": \"quadcopter\",
    \"timestamp\": \"$timestamp\",
    \"position\": {
      \"latitude\": ${drone_lat:0:10},
      \"longitude\": ${drone_lon:0:11},
      \"altitude\": ${drone_alt:0:6},
      \"heading\": $drone_heading
    },
    \"velocity\": {
      \"speed\": ${drone_speed:0:5},
      \"vertical_speed\": $(echo "s($counter * 0.3) * 2" | bc -l | awk '{printf "%.2f", $1}')
    },
    \"battery\": {
      \"percentage\": ${drone_battery:0:5},
      \"voltage\": $(echo "22.2 * ($drone_battery / 100)" | bc -l | awk '{printf "%.2f", $1}'),
      \"current\": $(echo "15 + (s($counter * 0.2) * 5)" | bc -l | awk '{printf "%.2f", $1}')
    },
    \"sensors\": {
      \"temperature\": $(echo "25 + (s($counter * 0.1) * 5)" | bc -l | awk '{printf "%.1f", $1}'),
      \"pressure\": $(echo "1013.25 - ($drone_alt / 10)" | bc -l | awk '{printf "%.2f", $1}'),
      \"gps_satellites\": $((12 + ($counter % 3)))
    },
    \"status\": \"in_flight\",
    \"message_id\": $counter
  }"
  
  echo "[$timestamp] Published drone telemetry (msg: $counter)"
  
  sleep 1
  
  # Robot Telemetry (simulating ground movement)
  counter=$((counter + 1))
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
  
  robot_x=$(echo "$counter * 0.5" | bc -l | awk '{printf "%.3f", $1}')
  robot_y=$(echo "s($counter * 0.05) * 10" | bc -l | awk '{printf "%.3f", $1}')
  robot_battery=$(echo "100 - ($counter * 0.03)" | bc -l | awk '{printf "%.2f", ($1 > 0) ? $1 : 0}')
  robot_speed=$(echo "1.2 + (s($counter * 0.1) * 0.3)" | bc -l | awk '{printf "%.2f", $1}')
  
  nats pub constellation.telemetry.robot "{
    \"device_id\": \"robot-042\",
    \"device_type\": \"ground_rover\",
    \"timestamp\": \"$timestamp\",
    \"position\": {
      \"x\": $robot_x,
      \"y\": $robot_y,
      \"z\": 0.0,
      \"theta\": $(echo "($counter * 1.5) % 360" | bc | awk '{printf "%.2f", $1}')
    },
    \"velocity\": {
      \"linear\": $robot_speed,
      \"angular\": $(echo "s($counter * 0.15) * 0.5" | bc -l | awk '{printf "%.3f", $1}')
    },
    \"battery\": {
      \"percentage\": ${robot_battery:0:5},
      \"voltage\": $(echo "24.0 * ($robot_battery / 100)" | bc -l | awk '{printf "%.2f", $1}'),
      \"current\": $(echo "8 + (s($counter * 0.2) * 3)" | bc -l | awk '{printf "%.2f", $1}'),
      \"estimated_runtime_minutes\": $(echo "$robot_battery * 1.8" | bc -l | awk '{printf "%.0f", $1}')
    },
    \"sensors\": {
      \"lidar_range\": $(echo "5 + (s($counter * 0.3) * 2)" | bc -l | awk '{printf "%.2f", $1}'),
      \"imu\": {
        \"acceleration\": {
          \"x\": $(echo "s($counter * 0.2) * 0.5" | bc -l | awk '{printf "%.3f", $1}'),
          \"y\": $(echo "c($counter * 0.2) * 0.3" | bc -l | awk '{printf "%.3f", $1}'),
          \"z\": 9.81
        },
        \"gyroscope\": {
          \"x\": $(echo "s($counter * 0.1) * 0.1" | bc -l | awk '{printf "%.3f", $1}'),
          \"y\": $(echo "c($counter * 0.1) * 0.1" | bc -l | awk '{printf "%.3f", $1}'),
          \"z\": $(echo "s($counter * 0.05) * 0.05" | bc -l | awk '{printf "%.3f", $1}')
        }
      },
      \"temperature\": $(echo "28 + (s($counter * 0.12) * 7)" | bc -l | awk '{printf "%.1f", $1}'),
      \"obstacle_detected\": $([ $((counter % 7)) -eq 0 ] && echo "true" || echo "false")
    },
    \"motor_status\": {
      \"left_wheel_rpm\": $(echo "150 + (s($counter * 0.15) * 30)" | bc -l | awk '{printf "%.0f", $1}'),
      \"right_wheel_rpm\": $(echo "150 + (c($counter * 0.15) * 30)" | bc -l | awk '{printf "%.0f", $1}')
    },
    \"status\": \"operational\",
    \"message_id\": $counter
  }"
  
  echo "[$timestamp] Published robot telemetry (msg: $counter)"
  
  sleep 1
done
