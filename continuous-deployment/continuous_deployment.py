# Built-In Modules
import os
import requests
import json
import traceback
import logging

# 3rd Party Modules
import jinja2
from rhmsg.activemq.consumer import AMQConsumer

# Local Modules
from sync2jira.mailer import send_mail
from sync2jira.main import load_config

# Global Variables
handlers = [
    'repotracker.container.tag.updated'
]
# Logging
log = logging.getLogger(__name__)
# OpenShift Related
TOKEN = os.environ['TOKEN']
STAGE_TOKEN = os.environ['STAGE_TOKEN']
ENDPOINT = os.environ['ENDPOINT']
# Message Bus Related
CERT = os.environ['CERT']
KEY = os.environ['KEY']
CA_CERTS = os.environ['CA_CERTS']
ACTIVEMQ_QUERY = os.environ['ACTIVEMQ_QUERY']
ACTIVEMQ_URL_1 = os.environ['ACTIVEMQ_URL_1']
ACTIVEMQ_URL_2 = os.environ['ACTIVEMQ_URL_2']
# Message Bus Query Related
ACTIVEMQ_REPO_NAME = os.environ['ACTIVEMQ_REPO_NAME']
# SEND_EMAILS
SEND_EMAILS = os.environ['SEND_EMAILS']

def main():
    """
    Main function to start listening
    """
    try:

        # Create our consumer
        log.info("Connecting to ACTIVEMQ as a consumer...")
        c = AMQConsumer(
            urls=(ACTIVEMQ_URL_1, ACTIVEMQ_URL_2),
            certificate=CERT,
            private_key=KEY,
            trusted_certificates=CA_CERTS
        )
        # Start listening
        log.info('Starting up CD service...')
        c.consume(
            ACTIVEMQ_QUERY,
            lambda msg, data: handle_message(msg, data)
        )

    except Exception as e :
        log.error(f"Error!\nException {e}\nSending email..")
        report_email('failure', 'Continuous-Deployment-Main', traceback.format_exc())


def handle_message(msg, data):
    """
    Handle incoming message
    :param Dict msg: Incoming message
    :param Dict data: Incoming data, if any
    :return:
    """
    msg_dict = json.loads(msg.body)
    log.info(f"Encountered message: {msg_dict}")
    status = None
    if msg_dict['repo'] == ACTIVEMQ_REPO_NAME:
        if msg_dict['tag'] == "master":
            status, ret = update_tag(master=True)
        elif msg_dict['tag'] == "stage":
            status, ret = update_tag(stage=True)
        elif msg_dict['tag'] == "openshift-build":
            status, ret = update_tag(openshift_build=True)
        elif msg_dict['tag'] == "sync-page":
            status, ret = update_tag(sync_page=True)
        else:
            return
        if status:
            report_email('success', namespace=msg_dict['tag'])
        else:
            report_email('failure', data=msg_dict)


def update_tag(master=False, stage=False, openshift_build=False, sync_page=False):
    """
    Update OpenShift master image when fedmsg topic comes in.

    :param Bool master: If we are tagging master
    :param Bool stage: If we are tagging stage
    :param Bool openshift_build: If we are tagging openshift-build
    :param Bool sync_page: If we are tagging sync_page
    :rtype (Bool, response):
    :return: (Indication if we updated out image on OpenShift, API call response)
    """
    # Format the URL
    # Note: Here we assume that we have a pod for openshift-build running under the pod for stage.
    if master:
        umb_url = f"https://{ENDPOINT}/apis/image.openshift.io/v1/namespaces/sync2jira/imagestreamtags/sync2jira:latest"
        namespace = 'sync2jira'
        name = 'sync2jira:latest'
        tag = 'latest'
    elif sync_page:
        umb_url = f"https://{ENDPOINT}/apis/image.openshift.io/v1/namespaces/sync2jira/imagestreamtags/sync2jira:sync-page"
        namespace = 'sync2jira'
        name = 'sync2jira-sync-page:latest'
        tag = 'sync-page'
    elif stage:
        umb_url = f"https://{ENDPOINT}/apis/image.openshift.io/v1/namespaces/sync2jira-stage/imagestreamtags/sync2jira-stage:latest"
        namespace = 'sync2jira-stage'
        name = 'sync2jira-stage:latest'
        tag = 'stage'
    elif openshift_build:
        umb_url = f"https://{ENDPOINT}/apis/image.openshift.io/v1/namespaces/sync2jira-stage/imagestreamtags/openshift-build:latest"
        namespace = 'sync2ijra-stage'
        name = 'openshift-build:latest'
        tag = 'openshift-build'
    else:
        raise Exception("No type passed")

    # Make our put call
    try:
        ret = requests.put(umb_url,
                           headers=create_header(namespace),
                           data=json.dumps({
                               "kind": "ImageStreamTag",
                               "apiVersion": "image.openshift.io/v1",
                               "metadata": {
                                   "name": name,
                                   "namespace": namespace,
                                   "creationTimestamp": None
                               },
                               "tag": {
                                   "name": "",
                                   "annotations": None,
                                   "from": {
                                       "kind": "DockerImage",
                                       "name": f"quay.io/redhat-aqe/sync2jira:{tag}"
                                   },
                                   "generation": 0,
                                   "importPolicy": {},
                                   "referencePolicy": {
                                       "type": "Source"
                                   }
                               },
                               "generation": 0,
                               "lookupPolicy": {
                                   "local": False
                               },
                               "image": {
                                   "metadata": {
                                       "creationTimestamp": None
                                   },
                                   "dockerImageMetadata": None,
                                   "dockerImageLayers": None
                               }
                           }))
    except Exception as e:
        log.error(f"Failure updating image stream tag.\nException: {e}")
        report_email('failure', namespace, e)
    if ret.status_code == 200:
        log.info(f"Tagged new image for {name}")
        return True, ret
    else:
        log.error(f"Failure updating image stream tag.\nResponse: {ret}")
        return False, ret


def report_email(type, namespace=None, data=None):
    """
    Helper function to alert admins in case of failure.

    :param String type: Type to be used
    :param String namespace: Namespace being used
    :param String data: Data being used
    """
    if SEND_EMAILS == '0':
        log.info(f"SEND_EMAILS set to 0 not sending email. Type: {type}. Namespace: {namespace}, Data: {data}")
        return
    # Load in the Sync2Jira config
    config = load_config()

    # Email our admins with the traceback
    templateLoader = jinja2.FileSystemLoader(searchpath='usr/local/src/sync2jira/continuous-deployment')
    templateEnv = jinja2.Environment(loader=templateLoader)

    # Load in the type of template
    if type is 'failure':
        template = templateEnv.get_template('failure_template.jinja')
        html_text = template.render(namespace=namespace, response=data)
    elif type is 'success':
        template = templateEnv.get_template('success_template.jinja')
        html_text = template.render(namespace=namespace)

    # Send mail
    send_mail(recipients=[config['sync2jira']['mailing-list']],
              cc=None,
              subject=f"Sync2Jira Build Image Update Status: {type}!",
              text=html_text)


def create_header(namespace):
    """
    Helper function to create default header
    :param string namespace: Namespace to indicate which token to use
    :rtype Dict:
    :return: Default header
    """
    if namespace in ['sync2jira-stage']:
        token = STAGE_TOKEN
    else:
        token = TOKEN
    return {
        'Authorization': f'Bearer {token.strip()}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }


if __name__ == '__main__':
    main()
