#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import time
import numpy as np
from pair_correlation import *

import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib

ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'


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
    
def email_results(conn,params,image_ids,file_anns):
    """
    E-mail the result to the user.

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    @param image_ids: A python list of the new image omero ids
    """

    if not params['Email_Results']:
        return

    image_names = list_image_names(conn, image_ids, file_anns)

    msg = MIMEMultipart()
    msg['From'] = ADMIN_EMAIL
    msg['To'] = params['Email_address']
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = '[OMERO Job] Pair correlation'
    msg.attach(MIMEText("""
New pair correlation results files created:

Format:
[parent project/datset] image id : image name : result filename

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()

def get_rectangles(conn, imageId):
    """
    Returns a list of (x, y, width, height, zStart, zStop, tStart, tStop)
    of each rectange ROI in the image scaled to nm
    
    @param conn:    the BlitzGateWay connection
    @param imageId: the id on the server of the image that holds the ROIs
    """

    rois = []

    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)
    physX = 1.0
    physY = 1.0
    for roi in result.rois:
        zStart = None
        zEnd = 0
        tStart = None
        tEnd = 0
        x = None
        for shape in roi.copyShapes():
            if type(shape) == omero.model.RectI:
                # check t range and z range for every rectangle
                t = shape.getTheT().getValue()
                z = shape.getTheZ().getValue()
                if tStart is None:
                    tStart = t
                if zStart is None:
                    zStart = z
                tStart = min(t, tStart)
                tEnd = max(t, tEnd)
                zStart = min(z, zStart)
                zEnd = max(z, zEnd)
                if x is None: # get x, y, width, height for first rect only
                    x = int(shape.getX().getValue())
                    y = int(shape.getY().getValue())
                    width = int(shape.getWidth().getValue())
                    height = int(shape.getHeight().getValue())
        # if we have found any rectangles at all...
        if zStart is not None:
            rois.append((x*physX, y*physY, width*physX, height*physY, zStart, zEnd, tStart, tEnd))

    return rois

def do_fit(data,rmax,solver,func,guess):
    try:
        fit = np.zeros(data.shape)
        result = np.zeros((data.shape[1],len(guess)))
        for c in range(data.shape[1]):
            curve,params = fit_correlation(data[:,c],rmax,solver,func,guess)
            fit[:,c] = curve
            result[c,:] = params
    except:
        fit = None
        result = None 
        
    return fit,result

def fit_paircorrelation(data,rmax,expo_fit,expo_guess,\
                        expogauss_fit,expogauss_guess):
    """
    Launches the pair correlation calculation
    see 'pair_correlation.py'
    
    @param data:              the image being processed as a multipage array
    @param rmax:              the maximum radius used in the correlation 
                              calculation
    @param expo_fit:          boolean indicating we are fitting an exponential
    @param expo_guess:        a list of parameters passed to the model 
    @param expogauss_fit:     boolean indicating we are fitting an exponential+gaussian
    @param expogauss_guess:   a list of parameters passed to the model      
    """
    if data.any():
        efit = eresult = egfit = egresult = None
        if expo_fit:
            efit,eresult = do_fit(data,rmax,fit_exponential,exponential,expo_guess)
                
        if expogauss_fit:
            egfit,egresult = do_fit(data,rmax,fit_exponential_gaussian,\
                                    exponential_gaussian,expogauss_guess)
    return efit,eresult,egfit,egresult

def cross_correlate(image_array,sizeC,roi,rmax):
    """
    Calculates the pair correlation function for coordinates in 
    user-defined rectangular region of interest
    
    @param image_array:    a generator of image planes
    @param sizeC:          how many channels in the image (1 or 2)
    @param roi:            the region of interest being processed
    @param rmax:           the maximum radius used in the correlation 
                           calculation
    """ 
    sizeZ = 1
    sizeT = 1
    data = []
    r = np.arange(rmax+1)
    g = np.zeros((r.shape[0],1))
    for z in range(sizeZ):
        for c in range(sizeC):
            for t in range(sizeT):
                data.append(image_array.next())
                
    # roi will be [xmin,xmax,ymin,ymax]
    r = [roi[1],roi[1]+roi[3],roi[0],roi[0]+roi[2]]
    corr,radius = pc_corr(data[0],data[1],r,rmax)
    g[:,0] = corr
    return g, radius

def auto_correlate(image_array,sizeC,roi,rmax):
    """
    Calculates the pair correlation function for coordinates in 
    user-defined rectangular region of interest
    
    @param image_array:    a generator of image planes
    @param sizeC:          how many channels in the image (1 or 2)
    @param roi:            the region of interest being processed
    @param rmax:           the maximum radius used in the correlation 
                           calculation
    """ 
    sizeZ = 1
    sizeT = 1
    r = np.arange(rmax+1)
    print 'size C',sizeC
    for z in range(sizeZ):
        g = np.zeros((r.shape[0],sizeC))
        for c in range(sizeC):
            for t in range(sizeT):
                data = image_array.next()
                # roi will be [xmin,xmax,ymin,ymax]
                r = [roi[1],roi[1]+roi[3],roi[0],roi[0]+roi[2]]
                corr,radius = pc_corr(data[:,:],data[:,:],r,rmax)
                g[:,c] = corr
    return g, radius

def process_data(conn,image,corr_func,rmax,expo_fit,expo_params,expogauss_fit,\
                                       expogauss_params):
    """
    Run the processing on each region of interest in the image
    
    @param conn:      the BlitzGateWay connection
    @param image:     the image being processed
    @param rmax:      the maximum radius used in the pair correlation 
                      calculation
    @param model:     if we are fitting what model is being used
    @param params:    a list of parameters passed to the model
    """  
    imageId = image.getId()
    pixels = image.getPrimaryPixels()
    imgW = image.getSizeX()
    imgH = image.getSizeY()
    rois = get_rectangles(conn,imageId)
    try:
        pix_size = pixels.getPhysicalSizeX()*1000
    except:
        message = 'No pixel size set on image!'
        return message,None
    
    rmax = int(rmax)
    for index, r in enumerate(rois):
        x, y, w, h, z1, z2, t1, t2 = r
        # Bounding box
        X = max(x, 0)
        Y = max(y, 0)
        X2 = min(x + w, imgW)
        Y2 = min(y + h, imgH)

        W = X2 - X
        H = Y2 - Y
        if (x, y, w, h) != (X, Y, W, H):
            print "\nCropping ROI (x, y, w, h) %s to be within image."\
                " New ROI: %s" % ((x, y, w, h), (X, Y, W, H))
            rois[index] = (X, Y, W, H, z1, z2, t1, t2)
            
    print "rois"
    print rois

    if len(rois) == 0:
        print "No rectangular ROIs found for image ID: %s" % imageId
        return

    output = []
    for r in rois:
        x, y, w, h, z1, z2, t1, t2 = r
        print "  ROI x: %s y: %s w: %s h: %s z1: %s z2: %s t1: %s t2: %s"\
            % (x, y, w, h, z1, z2, t1, t2)

        sizeC = image.getSizeC()
        z, t = 0, 0
        zctList = [(z, c, t) for c in range(sizeC)]
        planes = pixels.getPlanes(zctList)
        
        def plane_gen():
            for i,p in enumerate(planes):
                yield p
                
        pair_corr = {}
        if 'auto' in corr_func:
            g, radius = auto_correlate(plane_gen(), sizeC, r, rmax)
        elif 'cross' in corr_func:
            g, radius = cross_correlate(plane_gen(), sizeC, r, rmax)
            
        pair_corr['correlation'] = g
        pair_corr['radius'] = np.reshape(radius,(radius.shape[0],1))*pix_size
        
        if expo_fit or expogauss_fit:
            ecurve,eresults,egcurve,egresults = fit_paircorrelation(g,radius*pix_size,\
                                                       expo_fit,expo_params,\
                                                       expogauss_fit,expogauss_params)
            pair_corr['exponential_fit'] = ecurve
            pair_corr['exponential_params'] = eresults
            pair_corr['exponential+gaussian_fit'] = egcurve
            pair_corr['exponential+gaussiam_params'] = egresults
        else:
            pair_corr['exponential_fit'] = None
            pair_corr['exponential_params'] = None
            pair_corr['exponential+gaussian_fit'] = None
            pair_corr['exponential+gaussiam_params'] = None                         
        output.append(pair_corr)
        
    message = 'Successfully ran pair-correlation. '
    return message,output
                            
def run_processing(conn,script_params):
    """
    Collects params and starts the processing
    
    @param conn:          the BlitzGateWay connection
    @param script_params: the parameters collected from the script input
    """      
    file_anns = []
    message = ""
    
    rmax = script_params['Max_radius']
    expo_fit = script_params['Fit_exponential_model']
    expo_params = [script_params['Exponential_baseline'],\
                   script_params['Exponential_amplitude'],\
                   script_params['Exponential_decay']]
    expogauss_fit = script_params['Fit_exponential+gaussian_model']
    expogauss_params = [script_params['Density'],script_params['PSF'],\
                        script_params['Amplitude'],script_params['Decay'],\
                        script_params['Baseline']] 
         
    image_ids = script_params['IDs']
    for image in conn.getObjects("Image",image_ids):
        if not image:
            message = 'Could not find specified image'
            return message
        image_id = image.getId()
        sizeC = image.getSizeC()
        corr_func = script_params['Pair_correlation']
        if ('cross' in corr_func) and (sizeC != 2):
            return 'image should have two channels to cross-correlate' 
          
        message,output = process_data(conn,image,corr_func,rmax,expo_fit,expo_params,\
                                      expogauss_fit,expogauss_params)
        if output:
            file_name = "image%s_%s_correlation.csv" % (image_id,corr_func)
            with file(file_name, 'w') as outfile:
                outfile.write('# auto correlation data for %s ROIs and %s channels: \n' %\
                            (len(output), output[0]['correlation'].shape[1] ))
                
                for r, pair_corr in enumerate(output):
                    header = 'radius,'
                    data = np.concatenate((pair_corr['radius'],pair_corr['correlation']),axis=1)
                    for i in range(pair_corr['correlation'].shape[1]):
                        header += 'correlation,'
                    outfile.write('# Region of interest %s\n' % r)
                                
                    if pair_corr['exponential_fit'] is not None:
                        outfile.write('exponential fit params for ROI %s: \n' % r)
                        outfile.write('Baseline: %s \n' % pair_corr['exponential_params'][:,0])
                        outfile.write('Amplitude: %s \n' % pair_corr['exponential_params'][:,1])
                        outfile.write('Decay: %s \n' % pair_corr['exponential_params'][:,2])
                        for i in range(pair_corr['exponential_fit'].shape[1]):
                            header += 'fit,'
                        data = np.concatenate((data,pair_corr['exponential_fit']),axis=1)
                        
                    if pair_corr['exponential+gaussian_fit'] is not None:
                        outfile.write('exponential+gaussian fit params for ROI %s: \n' % r)
                        outfile.write('Density: %s \n' % pair_corr['exponential+gaussiam_params'][:,0])
                        outfile.write('PSF: %s \n' % pair_corr['exponential+gaussiam_params'][:,1])
                        outfile.write('Amplitude: %s \n' % pair_corr['exponential+gaussiam_params'][:,2])
                        outfile.write('Decay: %s \n' % pair_corr['exponential+gaussiam_params'][:,3])
                        outfile.write('Baseline: %s \n' % pair_corr['exponential+gaussiam_params'][:,4])
                        for i in range(pair_corr['exponential+gaussian_fit'].shape[1]):
                            header += 'fit,'
                        data = np.concatenate((data,pair_corr['exponential+gaussian_fit']),axis=1)                
                        
                    outfile.write(header[:-1] + '\n')    
                    np.savetxt(outfile, data, fmt='%-7.2f', delimiter=',', newline='\n')
                    
            new_file_ann, faMessage = script_util.createLinkFileAnnotation(
                conn, file_name, image, output="Pair correlation csv file",
                mimetype="text/csv", desc=None)
            if new_file_ann:
                file_anns.append(new_file_ann)
        
            if not file_anns:
                faMessage = "No Analysis files created. See 'Info' or 'Error' for"\
                    " more details"
            elif len(file_anns) > 1:
                faMessage = "Created %s csv (Excel) files" % len(file_anns)
            
            message += faMessage
            
    if script_params['Email_Results'] and file_anns:
        email_results(conn,script_params,image_ids,file_anns)
    
    return message

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service,
    passing the required parameters.
    """

    dataTypes = [rstring('Image')]
    model = [rstring('Exponential')]#,rstring('Gaussian+Exponential',rstring('Cosine+Exponential')]
    pc_corr = [rstring('auto'),rstring('cross')]

    client = scripts.client('Pair_Correlation_Function.py', """This script calculates the 
pair (auto or cross) correlation function for ROIs on PALM/STORM images.

This script should only be run on super resolved images where the 
physical pixel size has been set (e.g. CZI or OME-TIFF files).

This script uses code, translated to Python, that was provided in:

"Correlation Functions Quantify Super-Resolution Images 
and Estimate Apparent Clustering Due to Over-Counting"
Veatch et al, PlosONE, DOI: 10.1371/journal.pone.0031457

This paper should be referenced in any publication that
results from the use of this script.""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="ID of super resolved image to process"),
                            
    scripts.String("Pair_correlation", optional=False, grouping="03",
        description="Choose the type of pair correlation to perform", values=pc_corr, default="auto"),                            
        
    scripts.Int("Max_radius", optional=False, grouping="04",
        description="Maximum distance scale for calculation (in pixels)", default=50),
                            
    scripts.Bool("Fit_exponential_model", grouping="05", default=True,
        description="Choose model to fit to correlation data"),
                        
    scripts.Int("Exponential_amplitude", optional=False, grouping="05.1",
        description="Amplitude of exponential", default=30),

    scripts.Int("Exponential_decay", optional=False, grouping="05.2",
        description="Decay length of exponential (in nm)",default=80),
                            
    scripts.Int("Exponential_baseline", optional=False, grouping="05.3",
        description="Baseline of exponential",default=1),
                            
    scripts.Bool("Fit_exponential+gaussian_model", grouping="06", default=False,
        description="Choose model to fit to correlation data"),

    scripts.Int("Density", optional=False, grouping="06.1",
        description="Surface density of probe (1/um^2)", default=1),
                            
    scripts.Int("PSF", optional=False, grouping="06.2",
        description="sqrt(2)*PSF of the image (nm)", default=30),
                                                    
    scripts.Int("Amplitude", optional=False, grouping="06.3",
        description="Amplitude of exponential", default=30),

    scripts.Int("Decay", optional=False, grouping="06.4",
        description="Decay length of exponential (in nm)",default=80),
                            
    scripts.Int("Baseline", optional=False, grouping="06.5",
        description="Baseline of exponential",default=1),                            
                                                                                
    scripts.Bool(
        "Email_Results", grouping="07", default=False,
        description="E-mail the results"),
                        
    scripts.String("Email_address", grouping="07.1",
    description="Specify e-mail address"),                            
        
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
        
        #client.setOutput("Message", rstring("No plates created. See 'Error' or 'Info' for details"))
    finally:
        client.closeSession()

if __name__ == "__main__":
    run_as_script()
