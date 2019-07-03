## AutoScaling Termination Checker

This function receives instance-id from CloudWatch Events & SQS Queue, which will be triggered by AutoScaling Lifecycle Hook.  

Next, it takes the instance-id to query the recent 2 minutes maximum instance CPU usage in CloudWatch, decide whether to reset heartbeat or continue terminating.  

WARNING, this script is designed to only accpet one message - which means one instance-id at a time from SQS Queue,  
it does not process all queue at once. Please modify the code by yourself if you have the needs.  

Even better, if you could make a pull request.  

Disclaimer:  
This tool does not guarantee 100% accuracy, you should always keep an eye out on your own production environment. 

## Workflow  

1) Set a Lifecycle Hook in your Auto Scaling Group  
  2) Go to CloudWatch Events, and create rule that will respond to the Lifecycle Hook, for example:  
  
  ```
       {
         "source": [
           "aws.autoscaling"
         ],
         "detail-type": [
           "EC2 Instance-launch Lifecycle Action",
           "EC2 Instance-terminate Lifecycle Action"
         ],
         "detail": {
           "AutoScalingGroupName": [
             "your-group-name-here"
           ]
         }
       }
  ```
  
  3) In the rule target, set target to SQS queue, you will need to create your SQS queue first, FIFO recommended,  
     turn Content-Based Deduplication ON, MessageGroupId can be any  
  4) Put this Python 3 script on your Lambda, set `sqs_queue_url`, `cpu_trigger` in your Environment variables
  
```
     sqs_queue_url:  Define your FIFO SQS Queue URL in Lambda's Environment
                     Example: https://sqs.ap-southeast-1.amazonaws.com/123456789012/sqs_queue.fifo 
                     
     cpu_trigger:    Define the CPU Usage you wish to trigger the termination, unit: percent
                     When the CPU Usage is lower than this number, the instance will continue to be terminated
                     If not, the instance-id will be put back into SQS queue and wait for the next execution

     You can also put `slack_hook_url` in Environment variables, code will look for this, if it exists, success / error message will be sent
```

  5) Go to CloudWatch Events, choose Schedule Event Source, and run this Lambda script every minute, or any time frame you want  
  6) Done  

\* Reset heartbeat means abandon terminating in this case, but since AutoScaling termination does not support "Stop terminating", we will have to reset heartbeat, until the program sees the cpu usage being low enough, it will continue the termination process by sending either CONTINUE or ABANDON.  

Lastly, to execute this script on Lambda, you will need the following permissions:  

```
  cloudwatch:GetMetricStatistics
  sqs:ReceiveMessage
  sqs:DeleteMessage
  autoscaling:CompleteLifecycleAction
  autoscaling:RecordLifecycleActionHeartbeat
```
