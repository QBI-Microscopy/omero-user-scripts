#!/usr/bin/env python
# -*- coding: utf-8 -*-
import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
from threed_daostorm import find_peaks
import sa_library.parameters as params
import sa_utilities.std_analysis as std_analysis
import os
import re
import tempfile
from libtiff import TIFF
from numpy import zeros
import glob
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib

ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'

def delete_tmp(tmp_dir):
    try:
        for name in glob.glob("%s/*" % tmp_dir):
            os.remove(name)
        os.rmdir(tmp_dir)
    except:
        pass    
    
def delete_daostorm_params(ann):
    global daostorm_params_tmpdir
    file_path = os.path.join(daostorm_params_tmpdir, ann.getFile().getName())
    try:
        os.remove(file_path)
        os.rmdir(daostorm_params_tmpdir)
    except OSError:
        pass

def run_daostorm(conn,image_ids,input_dir,output_dir,parameters_file):
    message = ''
    images = glob.glob(input_dir + '/*.tif')
    print images
    parameters = params.Parameters(parameters_file)
    finder = find_peaks.initFindAndFit(parameters)
    file_anns = []
    for i,id in enumerate(image_ids):
        id = image_ids[i]
        omero_image = conn.getObject("Image",id)
        omero_image_name = omero_image.getName()[:-4]
        image = [name for name in images if omero_image_name in name][0]
        basename = os.path.basename(image)
        if '.ome' in basename:
            basename = basename[:-4]
        mlistname = output_dir + "/" + basename[:-4] + "_mlist.bin"
        
        std_analysis.standardAnalysis(finder,image,mlistname,parameters)

        output_files = os.path.isfile(mlistname)
        if output_files:
            # attach the result to the image

            new_file_ann, faMessage = script_util.createLinkFileAnnotation(
                conn, mlistname, omero_image, output="Daostorm analysis file",
                mimetype="application/octet-stream", desc=None)
            if new_file_ann:
                file_anns.append(new_file_ann)
        else:
            faMessage = 'Some output files were missing'

        if not file_anns:
            faMessage += "No Analysis files created. See 'Info' or 'Error' for"\
                " more details"
        elif len(file_anns) > 1:
            faMessage += "Created %s daostorm files" % len(file_anns)    
        message += faMessage           
    return message,file_anns
    
def download_image(image,input_dir):

    name,ext = os.path.splitext(image.getName())
           
    sizeX = image.getSizeX()
    sizeY = image.getSizeY()
    theZ = 0
    theC = 0
    
    if 'czi' in ext:
        sizeT = image.getSizeT()
        zctList = [ (theZ, theC, t) for t in range(sizeT) ]
    elif 'tif' in ext:
        sizeT = image.getSizeZ()
        zctList = [ (t, theC, theZ) for t in range(sizeT) ]
        
    planes = image.getPrimaryPixels().getPlanes(zctList)    # A generator (not all planes in hand)

    image_data = zeros((sizeT,sizeY,sizeX),dtype='uint16')
    for t, plane in enumerate(planes):
        if t == 0:
            print(plane.shape)
        image_data[t,:,:] = plane
    
    tif = TIFF.open(os.path.join(input_dir,'%s.tif' % name),mode='w')
    tif.write_image(image_data)

def downlaod_parameters(annotation):
    """
        Downloads the specified file and returns the path on the server
    """ 
    global daostorm_params_tmpdir
    
    daostorm_params_tmpdir = tempfile.mkdtemp(prefix='daostorm_params')

    file_path = os.path.join(daostorm_params_tmpdir, annotation.getFile().getName())
    f = open(str(file_path), 'w')
    print "\nDownloading file to", file_path, "..."
    try:
        for chunk in annotation.getFileInChunks():
            f.write(chunk)
    finally:
        f.close()
        print "File downloaded!"
    return file_path

def list_image_names(conn, ids, file_anns):
    """
    Builds a list of the image names
    
    @param conn: The BlitzGateway connection
    @param ids: Python list of image ids
    """
    image_names = []
    for i,image_id in enumerate(ids):
        img = conn.getObject('Image', image_id)
        if not img:
            continue

        ds = img.getParent()
        if ds:
            pr = ds.getParent()
        else:
            pr = None

        image_names.append("[%s][%s] Image %d : %s : %s" % (
                           pr and pr.getName() or '-',
                           ds and ds.getName() or '-',
                           image_id, os.path.basename(img.getName()),
                           file_anns[i].getFile().getName()))

    return image_names

def email_results(conn,params,image_ids,file_anns):
    """
    E-mail the result to the user.

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    @param image_ids: A python list of the new image omero ids
    """
    print params['Email_Results']
    if not params['Email_Results']:
        return

    image_names = list_image_names(conn, image_ids, file_anns)

    msg = MIMEMultipart()
    msg['From'] = ADMIN_EMAIL
    msg['To'] = params['Email_address']
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = '[OMERO Job] DAOSTORM'
    msg.attach(MIMEText("""
New daostorm results files created:

Format:
[parent project/datset][daostorm results] image id : image name : result filename

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()
    
def run_processing(conn, script_params):
    
    message = ""
    
    image_ids = script_params['IDs']
    if len(image_ids) > 5:
        message = 'Max number of datasets for batch exceeded (5)'
        return message
    
    for image in conn.getObjects("Image",image_ids):
        if not image:
            message = 'Could not find specified image'
            return message
        
    file_id = script_params['DAOSTORM_PARAMS']
    ann = conn.getObject("Annotation",file_id)
    if not ann:
        message = 'Could not find specified DAOSTORM parameters file'
        return message
    
    path_to_ann = ann.getFile().getPath() + '/' + ann.getFile().getName()
    name,ext = os.path.splitext(path_to_ann)
    if ('xml' in ext):
        #download the localisations data file
        parameters_file = downlaod_parameters(ann)
        
        input_dir = tempfile.mkdtemp(prefix='daostorm_input')
        output_dir = tempfile.mkdtemp(prefix='daostorm_output')
        
        for image in conn.getObjects("Image",image_ids):
            # download the image
            download_image(image,input_dir)
            
        message,file_anns = run_daostorm(conn, image_ids, input_dir, output_dir, parameters_file)
        
        if glob.glob('%s/*.bin'%output_dir) and script_params['Email_Results']:
            email_results(conn,script_params,image_ids,file_anns)
            
        delete_daostorm_params(ann)
        
        delete_tmp(input_dir)
        delete_tmp(output_dir)
        
    else:
        message = 'The parameters file must be xml'
            
    return message

def validate_email(conn, params):
    """
    Checks that a valid email address is present for the user_id

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    """
    userEmail = ''
    if params['Email_address']:
        userEmail = params['Email_address']
    else:
        user = conn.getUser()
        user.getName() # Initialises the proxy object for simpleMarshal
        dic = user.simpleMarshal()
        if 'email' in dic and dic['email']:
            userEmail = dic['email']

    params['Email_address'] = userEmail
    print userEmail
    # Validate with a regular expression. Not perfect but it will do
    return re.match("^[a-zA-Z0-9._%-]+@[a-zA-Z0-9._%-]+.[a-zA-Z]{2,6}$",
                    userEmail)

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """

    dataTypes = [rstring('Image')]

    client = scripts.client('DAOSTORM.py', """Run DAOSTORM analysis on a batch images. Make sure you attach an xml parameters file to at least one image
(this will be used for entire batch). Make sure you test your parameters offline before beginning. MAXIMUM NUMBER OF DATASETS FOR BATCH IS FIVE!""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="IDs of images on which to set pixel size").ofType(rlong(0)),
                            
    scripts.Int("DAOSTORM_PARAMS", optional=False, grouping="03",
        description="Annotation ID of DAOSTORM parameters xml file"),
                            
    scripts.Bool("Email_Results", grouping="04", default=True,
        description="E-mail the results"),
                            
    scripts.String("Email_address", grouping="04.1", description="Specify e-mail address"),                                       
        
    authors = ["Daniel Matthews", "QBI"],
    institutions = ["University of Queensland"],
    contact = "d.matthews1@uq.edu.au",
    )

    try:

        # process the list of args above.
        scriptParams = {}
        for key in client.getInputKeys():
            if client.getInput(key):
                scriptParams[key] = client.getInput(key, unwrap=True)

        print scriptParams
        
        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)

        if scriptParams['Email_Results'] and not validate_email(conn, scriptParams):
            client.setOutput("Message", rstring("No valid email address"))
            return
        
        # process images in Datasets
        message = run_processing(conn, scriptParams)
        client.setOutput("Message", rstring(message))
    finally:
        client.closeSession()

if __name__ == "__main__":
    run_as_script()