'''

AutoScaling Termination Checker

This function receives instance-id from CloudWatch Events & SQS Queue, which will be triggered by AutoScaling Lifecycle Hook.

Next, it takes the instance-id to query the recent 2 minutes maximum instance CPU usage in CloudWatch, decide whether to reset heartbeat or continue terminating.

WARNING, this script is designed to only accpet one message - which means one instance-id at a time from SQS Queue, 
it does not process all queue at once. Please modify the code by yourself if you have the needs.

Even better, if you could make a pull request.

Disclaimer:  
This tool does not guarantee 100% accuracy, you should always keep an eye out on your own production environment.

Workflow:
  1) Set a Lifecycle Hook in your Auto Scaling Group
  2) Go to CloudWatch Events, and create rule that will respond to the Lifecycle Hook, for example:
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
  3) In the rule target, set target to SQS queue, you will need to create your SQS queue first, FIFO recommended, 
     turn Content-Based Deduplication ON, MessageGroupId can be any
  4) Put this Python 3 script on your Lambda, set sqs_queue_url, cpu_trigger in your Environment variables, for details please look for description inside the codes
     You can also put slack_hook_url in Environment variables, code will look for this, if it exists, success / error message will be sent
  5) Go to CloudWatch Events, choose Schedule Event Source, and run this Lambda script every minute, or any time frame you want
  6) Done

* Reset heartbeat means abandon terminating in this case, but since AutoScaling termination does not support "Stop terminating", we will have to reset heartbeat,
  until the program sees the cpu usage being low enough, it will continue the termination process by sending either CONTINUE or ABANDON.

Lastly, to execute this script on Lambda, you will need the following permissions:
  cloudwatch:GetMetricStatistics
  sqs:ReceiveMessage
  sqs:DeleteMessage
  autoscaling:CompleteLifecycleAction
  autoscaling:RecordLifecycleActionHeartbeat

'''

import boto3
import json
import datetime
import os
import urllib.request

''' Define the CPU Usage you wish to trigger the termination, unit: percent
    When the CPU Usage is lower than this number, the instance will continue to be terminated
    If not, the instance-id will be put back into SQS queue and wait for the next execution
'''
cpu_trigger = float(os.environ['cpu_trigger'])

''' Define your FIFO SQS Queue URL in Lambda's Environment
    Example: https://sqs.ap-southeast-1.amazonaws.com/123456789012/sqs_queue.fifo 
'''
sqs_queue_url = os.environ['sqs_queue_url']

'''
'''

def json_serial(obj):
    ''' Convert datetime to string for json serializer
    '''
    if isinstance(obj, (datetime.datetime)):
        return obj.isoformat()
    raise TypeError ("Type %s not serializable" % type(obj))

def retrieve_instance_id(sqs_queue_url):
    ''' Retrieve CloudWatch Event from SQS Queue
    '''
    client = boto3.client('sqs')
    response = client.receive_message(
        QueueUrl            = sqs_queue_url,
        MaxNumberOfMessages = 1
    )

    ''' Check if any message returned, if not, abort by raising ValueError
    '''
    if 'Messages' in response.keys():
        pass
    else:
        raise ValueError("No message in SQS Queue.")
        
    sqs_body = response[u'Messages'][0][u'Body']
    event = json.loads(sqs_body)

    ''' Get ReceiptHandle for SQS message deletion
    '''
    global ReceiptHandle

    ReceiptHandle        = response[u'Messages'][0][u'ReceiptHandle']

    ''' Get all the useful information inside the CloudWatch Event
    '''
    global LifecycleActionToken
    global AutoScalingGroupName
    global LifecycleHookName
    global EC2InstanceId
    global LifecycleTransition
    ''' global NotificationMetadata
    '''

    LifecycleActionToken = event[u'detail'][u'LifecycleActionToken']
    AutoScalingGroupName = event[u'detail'][u'AutoScalingGroupName']
    LifecycleHookName    = event[u'detail'][u'LifecycleHookName']
    EC2InstanceId        = event[u'detail'][u'EC2InstanceId']
    LifecycleTransition  = event[u'detail'][u'LifecycleTransition']
    ''' NotificationMetadata = event[u'detail'][u'NotificationMetadata']
    '''

def query_cloudwatch_for_cpu_usage(EC2InstanceId):
    ''' Get the last 2 minutes maximum CPU Usage for certain EC2 from CloudWatch
        You will need certain permission to be included in the Lambda Role
    '''

    ''' Define time period of data you want to look into
        Unit: second
    '''
    time_period = 120

    d          = datetime.datetime.now()
    start      = d - datetime.timedelta(seconds=time_period)
    end        = d
    start_time = start.isoformat() + 'Z'
    end_time   = end.isoformat() + 'Z'

    client = boto3.client('cloudwatch')
    response = client.get_metric_statistics(
        Namespace  = 'AWS/EC2',
        MetricName = 'CPUUtilization',
        Dimensions = [
            {
            'Name': 'InstanceId',
            'Value': EC2InstanceId
            },
        ],
        Period     = time_period,
        StartTime  = start_time,
        EndTime    = end_time,
        Statistics = [
            'Maximum'
        ],
        Unit='Percent'
    )

    json_response = json.loads(json.dumps(response, default=json_serial))

    global EC2CpuUsage
    global EC2NotExist

    try:
        EC2CpuUsage = json_response[u'Datapoints'][0][u'Maximum']
    except:
        ''' If failed to retrieve EC2CpuUsage, then Datapoints - EC2 Instance might not exists.
            And if EC2 Instance does not exist, remove the message from SQS queue.
        '''
        sqs_client = boto3.client('sqs')

        sqs_response = sqs_client.delete_message(
            QueueUrl      = sqs_queue_url,
            ReceiptHandle = ReceiptHandle
        )

        raise ValueError ("EC2NotExist: Cannot query CloudWatch for " + EC2InstanceId + "'s latest CPU Usage, removed message from SQS queue.")

def usage_judgement(EC2CpuUsage):
    ''' If lower than cpu_trigger usage, return CONTINUE
        If higher than, return HEARTBEAT
    '''
    if EC2CpuUsage < cpu_trigger:
        var = "CONTINUE"
        return var
    else:
        var = "HEARTBEAT"
        return var

def terminate_lifecycle_action(return_var):
    ''' If CONTINUE, go on and tell Auto Scaling group to terminate the instance, then delete the message from SQS
        If HEARTBEAT, renew the action timeout in Auto Scaling group, do nothing to SQS and keep the message for next run
    '''
    as_client  = boto3.client('autoscaling')
    sqs_client = boto3.client('sqs')

    if return_var == "CONTINUE":
        response = as_client.complete_lifecycle_action(
            LifecycleHookName    =  LifecycleHookName,
            AutoScalingGroupName =  AutoScalingGroupName,
            LifecycleActionToken =  LifecycleActionToken,
            LifecycleActionResult= 'CONTINUE',
            InstanceId           =  EC2InstanceId
        )

        sqs_response = sqs_client.delete_message(
            QueueUrl      = sqs_queue_url,
            ReceiptHandle = ReceiptHandle
        )

        print (json.dumps(response, default=json_serial))
        raise ValueError (EC2InstanceId + "'s termination has been approved.")

    elif return_var == "HEARTBEAT":
        response = as_client.record_lifecycle_action_heartbeat(
            LifecycleHookName    =  LifecycleHookName,
            AutoScalingGroupName =  AutoScalingGroupName,
            LifecycleActionToken =  LifecycleActionToken,
            InstanceId           =  EC2InstanceId
        )

        print (json.dumps(response, default=json_serial))
        raise ValueError (EC2InstanceId + "'s CPU Usage is higher than approved rate, abandon termination.")

def slack_notification(e_message):
    ''' If slack_hook_url exists, this function will be trigger to send error report
    '''
    try:
        slack_hook_url = os.environ['slack_hook_url']
        message = str (e_message)
        payload = {
            'text':       message,
            'username':   u'Lambda runtime error',
            'icon_emoji': u':patamon:'
        }
        send_text = "payload=" + json.dumps(payload)

        request = urllib.request.Request(
            slack_hook_url,
            data = send_text.encode("utf-8"),
            method="POST"
        )
        with urllib.request.urlopen(request) as response:
            response_body = response.read().decode("utf-8")
    except Exception as e_slack:
        print ( "Exception happened when sending slack notification.")
        print (e_slack)

def lambda_handler(lambda_event, lambda_context):
    ''' CloudWatch Events processer
    '''
    try:
        retrieve_instance_id            (sqs_queue_url)
        query_cloudwatch_for_cpu_usage  (EC2InstanceId)
        return_var = usage_judgement    (EC2CpuUsage)
        terminate_lifecycle_action      (return_var)

    except Exception as e_general:
        print (e_general)
        if not str (e_general) == "No message in SQS Queue.":
            slack_notification (e_general)
        else:
            pass

