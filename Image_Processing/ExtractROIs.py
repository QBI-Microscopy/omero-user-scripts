#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Title: Extract ROIs from images
Description: Extracts multiple polygon or rectangle ROIs from an image or set of images
 and creates individual images from them with associated links to parent image.

__author__  Liz Cooper-Williams, QBI

 This script is based on components/tools/OmeroPy/scripts/omero/util_scripts/Images_From_ROIs.py
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

This script gets all the Rectangles from a particular image, then creates new
images with the regions within the ROIs, and saves them back to the server.



"""

import omero.model
import omero.scripts as scripts
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject, rint, rfloat
import omero.util.script_utils as script_utils
import omero.util.tiles
from os import path
import numpy as np
#from scipy import misc
#import matplotlib.pyplot as plt
from matplotlib.path import Path
import re
import time
startTime = 0


def printDuration(output=True):
    global startTime
    if startTime == 0:
        startTime = time.time()
    if output:
        print "Script timer = %s secs" % (time.time() - startTime)

""" Get x and y coordinates of outline of shape
    Input: omero.model.shape
    Output: array of x coords and corresponding array of y coords
"""
def getPolygonPoints(shape):
    bbox = shape.getPoints()
    pattern = re.compile('\D*(\d+,\d+)*')
    m = pattern.findall(bbox.getValue())
    xc = []
    yc = []
    for i in m:
        if i != '':
            mx = re.match('(\d+),(\d+)',i)
            xc.append(int(mx.group(1)))
            yc.append(int(mx.group(2)))
        else:
            break
    return xc,yc
    
""" Get top left coords and dimensions of bounding box of shape
    Input : omero.model.shape
    Output: X,Y (top left coords), width (px) and height (px)
"""       
def getBoundDimensions(shape):
    X = 0
    Y = 0
    width = 0
    height = 0
    # calculate bounding box dimensions from any shape
    if (type(shape) == omero.model.EllipseI):
        cx = int(shape.getCx().getValue()) #centre x
        cy = int(shape.getCy().getValue()) #centre y
        rx = int(shape.getRx().getValue()) #radius x
        ry = int(shape.getRy().getValue()) #radius y
        X = cx - rx
        Y = cy - ry
        width = 2 * rx
        height = 2 * ry
    elif type(shape) == omero.model.PolygonI:
        #SmartPolygonI sp = omero.model.SmartPolygonI(shape)
        #sp.asPoints() - bounding box
        #sp.areaPoints() - all xy points
        #regex: http://pythex.org/
        xc,yc = getPolygonPoints(shape)
        X = min(xc)
        Y = min(yc)
        width = max(xc) - min(xc)
        height = max(yc) - min(yc)        

    return X, Y, width, height

 
""" Get a mask for the shape (ellipse or polygon)
    Input: omero.model.shape, omero.image
    Output: mask of same dimensions as original image - crop as required
    """
def getPolygonMask(shape, img):
    x = []
    y = []    
    if (type(shape) == omero.model.EllipseI):
        cx = int(shape.getCx().getValue()) #centre x
        cy = int(shape.getCy().getValue()) #centre y
        rx = int(shape.getRx().getValue()) #radius x
        ry = int(shape.getRy().getValue()) #radius y
        # vertices of the ellipse
        #x = [cx-rx, cx, cx+rx, cx, cx-rx]
        #y = [cy, cy-ry, cy, cy+ry, cy]
        t = np.linspace(-np.pi,np.pi,100)
        x = cx + rx * np.cos(t)
        y = cy + ry * np.sin(t)
    elif type(shape) == omero.model.PolygonI:
         # vertices of the polygon
        x,y = getPolygonPoints(shape)
        
    xc = np.array(x)
    yc = np.array(y)
    xycrop = np.vstack((xc, yc)).T
    
    # xy coordinates for each pixel in the image
    #nr = img.getPrimaryPixels().getSizeY()
    #nc = img.getPrimaryPixels().getSizeX()
    nr = img.getSizeY()
    nc = img.getSizeX()
    #nr = img.getPrimaryPixels().getPhysicalSizeY().getValue()
    #nc = img.getPrimaryPixels().getPhysicalSizeX().getValue()
    print "Mask nr=", nr, " nc=", nc
    if (int(nr) + int(nc) >= 100000):
        print "Cannot create mask of this size: %d x %d " % (nr,nc)
        mask = None
    else:
        #plane = img.getPrimaryPixels().getPlane(0,0,0)
        #nr, nc = plane.shape
        ygrid, xgrid = np.mgrid[:nr, :nc]
        xypix = np.vstack((xgrid.ravel(), ygrid.ravel())).T
        
        # construct a Path from the vertices
        pth = Path(xycrop, closed=False)
        
        # test which pixels fall within the path
        mask = pth.contains_points(xypix)
        
        # reshape to the same size as the image
        #mask = mask.reshape(plane.shape)
        mask = mask.reshape((img.getPrimaryPixels().getSizeY(),img.getPrimaryPixels().getSizeX()))
        mask = ~mask
    return mask
"""
    Create a mask from ROI shape to match current tile
"""
def getOffsetPolyMaskTile(shape, pos_x, pos_y, tW, tH):
    x = []
    y = []    
    if (type(shape) == omero.model.EllipseI):
        cx = int(shape.getCx().getValue()) #centre x
        cy = int(shape.getCy().getValue()) #centre y
        rx = int(shape.getRx().getValue()) #radius x
        ry = int(shape.getRy().getValue()) #radius y
        # vertices of the ellipse
        #x = [cx-rx, cx, cx+rx, cx, cx-rx]
        #y = [cy, cy-ry, cy, cy+ry, cy]
        t = np.linspace(-np.pi,np.pi,100)
        x = cx + rx * np.cos(t)
        y = cy + ry * np.sin(t)
    elif type(shape) == omero.model.PolygonI:
         # vertices of the polygon
        x,y = getPolygonPoints(shape)
    
    xv = np.array(x)
    yv = np.array(y)
    #List of poly coords
    xycrop = np.vstack((xv, yv)).T
    # Get region of image
    xt = range(pos_x, pos_x + tW)
    yt = range(pos_y, pos_y + tH)
    
    xtile, ytile = np.meshgrid(xt, yt, sparse=False, indexing='xy')
    xypix = np.vstack((xtile.ravel(), ytile.ravel())).T
    pth = Path(xycrop, closed=False)
    mask = pth.contains_points(xypix)
    mask = mask.reshape(tH, tW)
    return ~mask

def hasPoints(shape):
    rtn = False
    
    if (type(shape) == omero.model.EllipseI):  
        if shape.getRx().getValue() > 0:
            rtn = True
    elif type(shape) == omero.model.RectangleI:
        if shape.getWidth().getValue() > 0:
            rtn = True
    elif type(shape) == omero.model.PolygonI:
        pts = getPolygonPoints(shape)
        #print "Shape getPoints:", pts[0]
        if len(pts[0]) > 2:
            rtn = True        
    if (not rtn):
        print "Skipping ROI as it is empty:" , shape.getId().getValue()
        
    return rtn
    
""" getShapes
    Input: active connection, image id
    Output: Returns a structure containing flexible shape variables eg shape['width']
    including a list of bounding box dimensions 
    (x, y, width, height, zStart, zStop, tStart, tStop) as shape['bbox']
    for each ROI shape in the image
    """
def getShapes(conn, imageId, image):
    rois = []
    #image = conn.getObject("Image", imageId)
    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)

    for roi in result.rois:
        print "ROI:  ID:", roi.getId().getValue()
        zStart = None
        zEnd = 0
        tStart = None
        tEnd = 0
        x = None
        for i,s in enumerate(roi.copyShapes()): #omero.model
            #ignore empty ROIs
            if (not hasPoints(s)):
                continue
            shape = {}
            shape['id'] = int(s.getId().getValue())
            shape['theT'] = int(s.getTheT().getValue())
            shape['theZ'] = int(s.getTheZ().getValue())
            # Determine 4D data for tiling
            if tStart is None:
                tStart = shape['theT']
            if zStart is None:
                zStart = shape['theZ']
            tStart = min(shape['theT'], tStart)
            tEnd = max(shape['theT'], tEnd)
            zStart = min(shape['theZ'], zStart)
            zEnd = max(shape['theZ'], zEnd)
            # Use label for new image filename
            if s.getTextValue():
                shape['ROIlabel'] = s.getTextValue().getValue()
            else:
                shape['ROIlabel'] = 'ROI_' + str(shape['id'])
            # Masks used to clear pixels outside shape
            shape['shape'] = s 
            shape['maskroi'] = None    
            if type(s) == omero.model.RectangleI:
                print "Found Rectangle: " + shape['ROIlabel']
                # check t range and z range for every rectangle
                shape['type'] = 'Rectangle'
                # Get region bbox
                x = int(s.getX().getValue())
                y = int(s.getY().getValue())
                width = int(s.getWidth().getValue())
                height = int(s.getHeight().getValue())
                shape['bbox'] = (x, y, width, height, zStart, zEnd, tStart, tEnd)
                shape['x'] = x
                shape['y'] = y
                shape['width'] = width
                shape['height'] = height
            elif type(s) == omero.model.EllipseI:
                print "Found Ellipse: " + shape['ROIlabel']
                #Get bounding box dimensions
                x, y, width, height = getBoundDimensions(s)
                shape['bbox'] = (x, y, width, height, zStart, zEnd, tStart, tEnd)
                shape['type'] = 'Ellipse'
                shape['cx'] = s.getCx().getValue()
                shape['cy'] = s.getCy().getValue()
                shape['rx'] = s.getRx().getValue()
                shape['ry'] = s.getRy().getValue()
                #Create mask - reverse axes for numpy
                #mask = getPolygonMask(s, image)
                #maskroi= mask[y:height+y,x:width+x]
                shape['maskroi'] = 1 
            elif type(s) == omero.model.PolygonI:
                
                x, y, width, height = getBoundDimensions(s)
                shape['bbox'] = (x, y, width, height, zStart, zEnd, tStart, tEnd)
                shape['maskroi'] = 1 
                shape['type'] = 'Polygon'
            else:
                print type(s), " Not supported by this script"
                shape={}

            if (shape):
                rois.append(shape)
    print "ROIS loaded:", len(rois)
    return rois

    
"""
    Process an image.
    Creates a 5D image representing the ROI "cropping" the
    original image via a Mask
    Image is put in a dataset if specified.
    """
def processImage(conn, image, parameterMap, datasetid=None):
    bgcolor = parameterMap['Background_Color']
    
    tagname = parameterMap['Use_ROI_label']
    
    imageId = image.getId()
    
    # Extract ROI shapes from image
    rois = getShapes(conn, imageId, image)
    iIds = []
    firstimage = None
    datasetdescription = ""
    roilimit = conn.getRoiLimitSetting()
    print "ROI limit = ", roilimit
    if len(rois) > 0:
        #Constants
        maxw = conn.getMaxPlaneSize()[0]
        print "Max plane size = ", maxw
        omeroToNumpy = {'int8': 'int8', 'uint8': 'uint8',
                        'int16': 'int16', 'uint16': 'uint16',
                        'int32': 'int32', 'uint32': 'uint32',
                        'float': 'float32', 'double': 'double'}        
        #Check size of parent image - LIMIT:?
        imgW = image.getSizeX()
        imgH = image.getSizeY()
        print "ID: %s \nImage size: %d x %d " % (imageId, imgW, imgH)
                
        imageName = image.getName()
        
        updateService = conn.getUpdateService()    
        pixelsService = conn.getPixelsService()
        queryService = conn.getQueryService()
        renderService = conn.getRenderingSettingsService()
       # rawPixelsStore = conn.c.sf.createRawPixelsStore()
       # containerService = conn.getContainerService()
        
        print "Connection: Got services..."
        
        pixels = image.getPrimaryPixels()
        
        # Check image data type
        imgPtype = pixels.getPixelsType().getValue()
        # omero::model::PixelsType
        pixelsType = queryService.findByQuery(
            "from PixelsType as p where p.value='%s'" % imgPtype, None)
        if pixelsType is None:
            raise Exception(
                "Cannot create an image in OMERO from numpy array "
                "with dtype: %s" % imgPtype)
        # uint8 = [0,255], uint16 = [0,65535],  [-32767,32768] for int16
        whites = {'uint8':255, 'int8':255, 'uint16': 65535, 'int16': 32768}
      #  tile_max = whites[imgPtype]
      #  tile_min = 0.0 
        if (bgcolor == 'White'):
            if (imgPtype in whites):
                bgcolor = whites[imgPtype]
            else:
                bgcolor = 255 #default
#        elif(bgcolor == 'MaxColor'):
#            bgcolor = tile_max
#        elif(bgcolor == 'MinColor'):
#            bgcolor = tile_min
        else:
            bgcolor = 0.0
    
        # Process ROIs      
        for r in rois:
            x, y, w, h, z1, z2, t1, t2 = r['bbox']

            print "  ROI x: %s y: %s w: %s h: %s z1: %s z2: %s t1: %s t2: %s"\
                % (x, y, w, h, z1, z2, t1, t2)

            # need a tile generator to get all the planes within the ROI
            sizeZ = z2-z1 + 1
            sizeT = t2-t1 + 1
            sizeC = image.getSizeC()
            zctTileList = []
            tile = (x, y, w, h)
            # Generate mask for ROI
            maskroi = r['maskroi']
            mask = None
            shape = r['shape']
                #else:
                #    polycoords = getPolygonPoints(shape)
            
            # LARGE FILES create tiles within max px limits - even tiles
            tilefactor = int(w/maxw)
            tileWidth = w / (tilefactor + 1)
            tilefactor = int(h/maxw)
            tileHeight = h / (tilefactor + 1)
            tileTotal = w/tileWidth * h/tileHeight
            print "Tilewidth=", tileWidth, " Tileheight=", tileHeight , " Tilecount=", tileTotal
            tiles = []
            maskList = []
            
            if (tileTotal > 1):
                print "Generating %d subtiles ..." % tileTotal
                for i, pos_y in enumerate(range(y, y + h, tileHeight)): 
                    tH = min(h - (tileHeight * i), tileHeight)
                    for j, pos_x in enumerate(range(x, x + w, tileWidth)): 
                        tW = min(w - (tileWidth * j), tileWidth)
                        area = (pos_x, pos_y, tW, tH)
                        tiles.append(area)
                        if(maskroi is not None):
                             masktile = getOffsetPolyMaskTile(shape, pos_x, pos_y, tW, tH)
                             maskList.append(masktile)
#                            m1 = mask
#                            masktile = m1[pos_y:tH + pos_y, pos_x:tW + pos_x]
#                            maskList.append(masktile)
                        
                            
            else:
                tiles.append(tile)
                if (maskroi is not None):
                    mask = getPolygonMask(shape, image) #whole image
                    if (mask is not None):
                        maskroi= mask[y:h+y,x:w+x] #cropped to ROI
                
            print "generating zctTileList..."
            for z in range(z1, z2 + 1):
                for c in range(sizeC):
                    for t in range(t1, t2 + 1):
                        for tile in tiles:
                             zctTileList.append((z, c, t, tile))
            
            #Generators 
            def tileGen(mask=None):
                for i, t in enumerate(pixels.getTiles(zctTileList)):
                    if(mask is not None):
                        #print(t.shape)
                        t[mask]=bgcolor
                        #plt.imshow(t)
                        #plt.show()
                    yield t
            
            
            def getImageTile(tileCount):
                print "tileCount=", tileCount
                p1 = tileList[tileCount]
                p = np.zeros(p1.shape, dtype=convertToType)
                p += p1
                if(len(maskList) > 0 and tileCount <= len(maskList) * len(channelList)):
                    maski = tileCount % len(maskList)
                    print "mask idx:", maski
                    m1 = maskList[maski]
                    print "mask:", m1.shape
                    p[m1] = bgcolor
                            
               # return p.tobytes() # numpy 1.9 +
                return p.min(),p.max(),p.tostring() # numpy 1.8-
              
            # Set image name for new image
            (imageName,ext) = path.splitext(image.getName())
            if (tagname and len(r['ROIlabel']) > 0):
                imageName = imageName + '_'+ r['ROIlabel'] + ext
            else:
                roid = r['id']
                imageName = imageName + '_'+ str(roid) + ext
                #DONT CREATE TAGS
                tagname = None
            # Add a description for the image
            description = "Created from image:\n  Name: %s\n  ROIimage: %s\n  Image ID: %d"\
                " \n x: %d y: %d" % (image.getName(), imageName, imageId, x, y)
            print description
            # Due to string limit with BlitzGateway - subtile image
            # Currently this is VERY slow - needs to improve
            if (tileTotal > 1):
                channelList = range(sizeC)
                #Create empty image then populate pixels
                iId = pixelsService.createImage(
                            w,
                            h,
                            sizeZ,
                            sizeT,
                            channelList,
                            pixelsType,
                            imageName,
                            description,
                            conn.SERVICE_OPTS)
               
                print "Empty Image created with %d x %d" % (w,h)
                newImg = conn.getObject("Image", iId)
                pid = newImg.getPixelsId()
                print "New Image pid: ", pid
           #     rawPixelsStore.setPixelsId(pid, True, conn.SERVICE_OPTS)
                #print "New Image Id = %s" % newImg.getId()
                convertToType = getattr(np, omeroToNumpy[imgPtype])
                print "type=", convertToType
                #Run once - list of generators
                tileList = list(pixels.getTiles(zctTileList))
                print "Size of tilelist=", len(tileList)
                print "Size of masklist=", len(maskList)
                channelsMinMax = []
                
                class Iteration(omero.util.tiles.TileLoopIteration):
                
                    def run(self, data, z, c, t, x, y, tileWidth, tileHeight, tileCount):
                        # dimensions re new empty image same as ROI
                        print "Iteration:z=", z, " c=", c, " t=", t, " x=", x,\
                        " y=", y, " w=", tileWidth, " h=", tileHeight, \
                        " tcnt=", tileCount
                        #Get pixel data from image to load into these coords                    
                        minValue,maxValue,tile2d = getImageTile(tileCount)
                        
                        # first plane of each channel
                        if len(channelsMinMax) < (c + 1):
                            channelsMinMax.append([minValue, maxValue])
                        else:
                            channelsMinMax[c][0] = min(
                                channelsMinMax[c][0], minValue)
                            channelsMinMax[c][1] = max(
                                channelsMinMax[c][1], maxValue)
                        print "generated tile2d ...setting data"
                        data.setTile(tile2d, z, c, t, x, y, tileWidth, tileHeight)
                        
                
                #Replace pixels in empty image which is same size as ROI
                print "Generating new image from RPSTileLoop"
                loop = omero.util.tiles.RPSTileLoop(conn.c.sf, omero.model.PixelsI(pid, False))
                times = loop.forEachTile(tileWidth, tileHeight, Iteration())            
                print times, " loops"
                for theC, mm in enumerate(channelsMinMax):
                    pixelsService.setChannelGlobalMinMax(
                        pid, theC, float(mm[0]), float(mm[1]), conn.SERVICE_OPTS)
                                
            else:
                # Use this method for smaller images/tiles 
                print "Generating new image from NumpySeq"
                newImg = conn.createImageFromNumpySeq(
                   tileGen(maskroi), imageName,
                   sizeZ=sizeZ, sizeC=sizeC, sizeT=sizeT,
                   sourceImageId=imageId,
                   description=rstring(description),
                   dataset=None)
            
            if newImg is not None:
                print "New Image Id = %s" % newImg.getId()
                if (tagname):
                   tagAnn = omero.gateway.TagAnnotationWrapper(conn)
                   tagAnn.setValue(str(r['ROIlabel']))
                   tagAnn.save()
                   newImg.linkAnnotation(tagAnn)
                # Link to dataset    
                if (datasetid is None):
                    print "Dataset id is not set - getting parent"
                    datasetid = image.getParent().getId()
                
                link = omero.model.DatasetImageLinkI()
                link.parent = omero.model.DatasetI(datasetid, False)
                link.child = omero.model.ImageI(newImg.getId(), False)
                link = updateService.saveAndReturnObject(link)
                if (link is not None):
                    print "New image linked to dataset"
                else:
                    print "ERROR: New image dataset link failed"
                    
                
                # for return - just one           
                if firstimage is None:
                   firstimage = newImg._obj
                   print "Setting first image"
                   #datasetdescription = "Images in this Dataset are from ROIs of parent Image:\n"\
                   #    "  Name: %s\n  Image ID: %d" % (image.getName(), image.getId()) 
                
                #BUG IN createImageFromNumpy doesn't save description - try again here  - OK
                if (len(newImg.getDescription()) <=0):
                    newImg = conn.getObject("Image", newImg.getId())
                    newImg.setDescription(description) 
                    updateService.saveObject(newImg._obj,conn.SERVICE_OPTS) #NB don't use return function      
                
                iIds.append(newImg.getId())
            
        # Apply rnd settings of the source image to new images.
        #Class parameter for changing settings must be in {Project, Dataset, Image, Plate, Screen, PlateAcquisition, Pixels}, not class ome.model.display.Thumbnail
        
        print "Applying rendering settings"
        renderService.applySettingsToSet(image.getId(), 'Image', iIds)
        #renderService.applySettingsToSet(image.getPrimaryPixels().getId(), 'Pixels', iIds)
        #containerService.getImages(
        #        "Image", [imageId], None, self.SERVICE_OPTS)[0], convertToType
        
    else:
        print "ERROR: No new images created from Image ID %d." % image.getId()
        
    return firstimage, datasetdescription, iIds 


def makeImagesFromRois(conn, parameterMap):
    """
    Processes the list of Image_IDs, either making a new
    dataset for new images or adding to the parent dataset,
    with new images extracted from (optionally labelled) ROIs 
    on the parent images.
    """

    dataType = parameterMap["Data_Type"]

    message = ""

    # Get the images to process
    objects, logMessage = script_utils.getObjects(conn, parameterMap)
    message += logMessage
    if not objects:
        return None, message

    # Either images or datasets
    if dataType == 'Image':
        images = objects
    else:
        images = []
        for ds in objects:
            images += ds.listChildren()

    imageIds = [i.getId() for i in images]
    #imageIds = [i.getId() for i in images if (i.getROICount() > 0)]
    print "Selected %d images for processing" % len(imageIds)
    
    #Generate dataset to add images to
    updateService = conn.getUpdateService()
    roiService = conn.getRoiService()
    
    datasetName = parameterMap['Container_Name']
    datasetid = None
    link = None
    dataset = None
    datasetdescription = "Images in this Dataset are generated by script: ExtractROIs\n"
    if (len(datasetName) > 0):
        #Assume all new images to be linked here
        dataset = omero.model.DatasetI()
        dataset.name = rstring(datasetName)
        # TODO update description after adding images in case no ROIS found
        for img in images:
            result = roiService.findByImage(img.getId(), None)
            if (len(result.rois) > 0):
                datasetdescription += "\nImages in this Dataset are from ROIs of parent Image:\n"\
                       "  Name: %s\n  Image ID: %d" % (img.getName(), img.getId())
        dataset.description = rstring(datasetdescription)
        dataset = updateService.saveAndReturnObject(dataset)
        datasetid = dataset.getId()
        #Link this to parent project
        parentDataset = images[0].getParent()
        if parentDataset and parentDataset.canLink():
            print "Linking to parent project"
            project = parentDataset.getParent()
            link = omero.model.ProjectDatasetLinkI()
            link.setParent(omero.model.ProjectI(project.getId(), False))
            link.setChild(dataset)
            updateService.saveObject(link)
        
        print "Dataset created: %s Id: %s" % (datasetName, int(datasetid.getValue()))   
    
    newImages = [] #ids of new images
    #newDatasets = []
    notfound = []
    
    for iId in imageIds:
        image = conn.getObject("Image", iId)
        if image is None:
            notfound.append(str(iId))
            next
        firstimg, desc, imageIds = processImage(conn, image, parameterMap, datasetid)
        if firstimg is not None:
            newImages.extend(imageIds)
            datasetdescription += "\nDataset:\n" 
            datasetdescription += desc
            print desc
        #newDatasets.append(datasetname)
    
    message += "Created %d new images" % len(newImages)
    if (datasetid is None):
        message += " in parent dataset"
    else:
        message += " in new dataset %s" % datasetName
        
            
    if link is None:
        message += " - unable to link to parent project"
    if (len(notfound) > 0):
        message += " - ids not found: %s" % notfound
    message += "."

    robj = (len(newImages) > 0) and firstimg or None
    return robj,message


def runAsScript():
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """
    printDuration(False)    # start timer
    dataTypes = [rstring('Dataset'), rstring('Image')]
    #bgTypes = [rstring("Black"),rstring("White"),rstring("MaxColor"),rstring("MinColor")]
    bgTypes = [rstring("Black"),rstring("White")]
        
    client = scripts.client(
        'Extract ROIs',
        """Extract Images from the regions defined by ROIs. \
        Updated script: 29 Feb 2016
        Accepts: Rectangle, Ellipse, Polygon Shapes \

        Outputs: Multiple ROIs produced as separate images with option to use ROI labels in filenames and tags \
        
        Replaces: Images from ROIs (Advanced) 

        Limitations:  Images from large ROIs (>12K x 12K px) cannot be exported (under development)

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
            "Container_Name", grouping="3",
            description="Put Images in new Dataset with this name",
            default="ExtractedROIs"),

        scripts.Bool(
            "Use_ROI_label", grouping="4", default=False,
            description="Use ROI labels in filename and as tag"),
        
        scripts.Bool(
            "Clear_Outside_Polygon", grouping="5", default=False,
            description="Clear area outside of polygon ROI (default is black)"),

        scripts.String(
            "Background_Color", grouping="5.1", default="Black",
            description="Background fill colour", values=bgTypes),
        
        

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

        robj, message = makeImagesFromRois(conn, parameterMap)

        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj))

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
                   'IDs': [8],
                   'Container_Name': 'ROIs',
                   'Clear_Outside_Polygon': True,
                   'Background_Color': 'White' ,
                   'Use_ROI_label': True,
                  }
    try:
        # Params from client
        #parameterMap = client.getInputs(unwrap=True)
        print parameterMap
        
        # create a wrapper so we can use the Blitz Gateway.
        #conn = BlitzGateway(client_obj=client)
        
        robj, message = makeImagesFromRois(conn, parameterMap)
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