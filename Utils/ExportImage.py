#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Title: Export large images
Description: Exports images larger than 12px x 12px.

__author__  Liz Cooper-Williams, QBI

 This script is based on components/tools/OmeroPy/scripts/omero/util_scripts/Export.py
 Written by Will Moore &nbsp;&nbsp;&nbsp;&nbsp;
<a href="mailto:will@lifesci.dundee.ac.uk">will@lifesci.dundee.ac.uk</a>
-----------------------------------------------------------------------------
  Copyright (C) 2006-2014 University of Dundee. All rights reserved.


  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 2 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License along
  with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

------------------------------------------------------------------------------
"""

import omero.model
import omero.scripts as scripts
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject
import omero.util.script_utils as script_utils
from omero.constants.namespaces import NSCREATED, NSOMETIFF
from os import path, remove, mkdir, rmdir
import shutil
import time


startTime = 0


def printDuration(output=True):
    global startTime
    if startTime == 0:
        startTime = time.time()
    if output:
        print "Script timer = %s secs" % (time.time() - startTime)

def saveAs(image,imageName,formatType):
#==============================================================================
#     import ImageFile
#     im = Image.frombuffer(mode, size, data, "raw", mode, 0, 1)
#     image = Image.fromstring(
#         mode, size, data, "raw",
#         raw mode, stride, orientation
#         )
#     fp = open(image, "rb")
# 
#     p = ImageFile.Parser()
# 
#     while 1:
#         s = fp.read(1024)
#         if not s:
#             break
#         p.feed(s)
#     
#     im = p.close()
#==============================================================================
    
    message = ""    
    try:
        image.save(imageName,formatType)
        print "Image saved: ", imageName
    except IOError:
        message = "Error: IO error: problem saving image"
        if path.exists(imageName):
            remove(imageName)
    return message
        

def getImageName(image, formatType, folder_name=None):
    name = path.basename(image.getName())
    extension = formatType.lower()
    imgName = "%s.%s" % (name, extension)
    if folder_name is not None:
        imgName = path.join(folder_name, imgName)
    # check we don't overwrite existing file
    i = 1
    pathName = imgName[:-(len(extension)+1)]
    while path.exists(imgName):
        imgName = "%s_(%d).%s" % (pathName, i, extension)
        i += 1

    print "  Saving file as: %s" % imgName
    return imgName
    
def createZipFile(target, filelist):
    import zipfile 
    import glob
    """
    Creates a ZIP recursively from a given base directory.

    @param target:      Name of the zip file we want to write E.g.
                        "folder.zip"
    @param base:        Name of folder that we want to zip up E.g. "folder"
    """
    base = path.dirname(filelist[0])    
    zip_file = zipfile.ZipFile(target, 'w')
    message = ""
    try:
        files = path.join(base, "*")        
        for name in glob.glob(files):
            zip_file.write(name, path.basename(name), zipfile.ZIP_DEFLATED)
        print "Zipfile written to ", target
        #clean up images        
        shutil.rmtree(base)
        print "Temp dir removed: ", base
    except RuntimeError:
        message = "Error: Runtime error during zipfile creation"
        
    finally:
        zip_file.close()
        
    return message

def saveImages(conn, scriptParams):
    dataType = scriptParams['Data_Type']
    formatType = scriptParams['Format']    
    dirpath='tmp' #Use Root tmp dir so gets cleaned up
    unique_folder = 'Exportfiles' + str(time.time()) #random
    imagepath = path.join(dirpath, unique_folder)
    imagepath = path.sep + imagepath + path.sep
    print "Imagepath=", imagepath
    # Get the images or datasets
    message = ""
    objects, logMessage = script_utils.getObjects(conn, scriptParams)
    message += logMessage
    parent = objects[0] #??
    if not objects:
        return None, message
    
    if dataType == 'Dataset':
        images = []
        for ds in objects:
            images.extend(list(ds.listChildren()))
        if not images:
            message += "No image found in dataset(s)"
            return None, message
    else:
        images = objects

    imageIds = [i.getId() for i in images]
    print "Selected %d images for processing" % len(imageIds)
    #Create download folder
    if len(imageIds) > 0:
        mkdir(imagepath)
        #chdir(imagepath)
    imagefilenames =[]
    for iId in imageIds:
        img = conn.getObject("Image", iId)
        
        if img is not None:
            z = img.getSizeZ() / 2
            t = 0 
            saveimage = img.renderImage(z,t) # returns PIL Image jpeg
            imageFilename = getImageName(img, formatType.lower(), imagepath)
            imagefilenames.append(imageFilename)            
            message += saveAs(saveimage,imageFilename,formatType)
        
    #zip output files
    zipfilename = path.join(dirpath, unique_folder)
    zipfilename = path.sep + zipfilename + ".zip"
    message += createZipFile(zipfilename, imagefilenames)

    mimetype = 'application/zip'
    outputDisplayName = "Image export zip"
    namespace = NSCREATED + "/QBI/Utils/ExportImage"
    
    fileAnnotation, annMessage = script_utils.createLinkFileAnnotation(
        conn, zipfilename, parent, output=outputDisplayName, ns=namespace,
        mimetype=mimetype)
    message += annMessage
    return fileAnnotation, message         
                
def runAsScript():
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """
    printDuration(False)    # start timer
    dataTypes = [rstring('Dataset'), rstring('Image')]
    formatTypes = [rstring('TIFF'), rstring('PNG'), rstring('JPEG')]
    client = scripts.client(
        'Simple Images Exporter',
        """Can export Images larger than 12px x 12px \
        Updated script: 15 Mar 2016
        
        Accepts: Image Ids, Dataset Id \
        
        Supports: TIFF, PNG and JPEG only at this stage \
        
        Outputs: Zipfile of images to download \
        
        Extends: Batch_Image_Export.py (best for smaller images - more options)

        Limitations:  For very large images recommend one image at a time - allow 20 mins

""",

        scripts.String(
            "Data_Type", optional=False, grouping="1",
            description="Select a 'Dataset' of images or specific images with these IDs", 
                values=dataTypes, default="Image"),

        scripts.List(
            "IDs", optional=False, grouping="2",
            description="List of Dataset IDs or Image IDs to "
            " process.").ofType(rlong(0)),
        
        scripts.String(
            "Format", optional=False, grouping="1",
            description="Select format for exported images", 
                values=formatTypes, default="TIFF"),

        version="1.0",
        authors=["Liz Cooper-Williams", "QBI Software"],
        institutions=["Queensland Brain Institute", "The University of Queensland"],
        contact="e.cooperwilliams@uq.edu.au",
    )

    try:
        # Params from client
        parameterMap = client.getInputs(unwrap=True)
        print parameterMap

        # create a wrapper so we can use the Blitz Gateway.
        conn = BlitzGateway(client_obj=client)

        robj, message = saveImages(conn, parameterMap)

        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj._obj))

    finally:
        client.closeSession()
        printDuration()

def runAsTest():
    """
    Test script locally with preset id.
    """
    #connect to OMERO server
    user = 'root'
    pw = 'omero'
    host = 'localhost'
    
    conn = BlitzGateway(user, pw, host=host, port=4064)
    connected = conn.connect()
    # Check if you are connected.
    # =============================================================
    if not connected:
        import sys
        print "Error: Connection not available, check VM is running.\n"
        sys.exit(1)
    else:
        print "Succesfully Connected to ", host    
    printDuration(False)    # start timer
    
    parameterMap ={'Data_Type' :'Image', 
                    'IDs': [1403],
                    'Format' : 'TIFF'
                  }
    try:
        # Params from client
        #parameterMap = client.getInputs(unwrap=True)
        print parameterMap
        
        # create a wrapper so we can use the Blitz Gateway.
        #conn = BlitzGateway(client_obj=client)
        
        robj, message = saveImages(conn, parameterMap)
        print message
        if robj is not None:
            #print robject(robj)
            print "Robj is OK"

        
    finally:
        conn._closeSession()
        printDuration() 



if __name__ == "__main__":
    runAsScript()
    #runAsTest()