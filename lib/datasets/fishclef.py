# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------

import os
import errno
from datasets.imdb import imdb
import xml.dom.minidom as minidom
import numpy as np
import scipy.sparse
import scipy.io as sio
import utils.cython_bbox
import cPickle
import subprocess
import uuid
import xmltodict
import cv2

USE_ORIGINAL_IMAGES = False

class fishclef(imdb):
    def __init__(self, image_set, devkit_path):
        imdb.__init__(self, image_set)
        self._image_set = image_set
        self._devkit_path = devkit_path
        self._data_path = os.path.join(self._devkit_path, 'data')
        self._classes = ('__background__', # always index 0
                       'abudefduf vaigiensis', 'acanthurus nigrofuscus', 'amphiprion clarkii', 'chaetodon lunulatus',
                       'chaetodon speculum', 'chaetodon trifascialis', 'chromis chrysura', 'dascyllus aruanus', 'dascyllus reticulatus',
                       'hemigymnus melapterus', 'myripristis kuntee', 'neoglyphidodon nigroris', 'pempheris vanicolensis',
                       'plectrogly-phidodon dickii', 'zebrasoma scopas', 'other')

        self._class_to_ind = dict(zip(self.classes, xrange(self.num_classes)))
        self._image_ext = ['.jpg', '.png', '.bmp']
        self._image_index = self._load_image_set_index()
        self._salt = str(uuid.uuid4())
        self._comp_id = 'comp4'

        # Specific config options
        self.config = {'cleanup'  : True,
                       'use_salt' : True,
                       'top_k'    : 2000,
                       'use_diff' : False,
                       'rpn_file' : None}

        print ("Number of classes: %d" % (self.num_classes))

        assert os.path.exists(self._devkit_path), \
                'Devkit path does not exist: {}'.format(self._devkit_path)
        assert os.path.exists(self._data_path), \
                'Path does not exist: {}'.format(self._data_path)

    def image_path_at(self, i):
        """
        Return the absolute path to image i in the image sequence.
        """
        return self.image_path_from_index(self._image_index[i])

    def image_path_from_index(self, index):
        """
        Construct an image path from the image's "index" identifier.
        """
        for ext in self._image_ext:
            if USE_ORIGINAL_IMAGES:
                image_path = os.path.join(self._data_path, 'Images', index + '-orig' + ext) # Added -orig
            else:
                image_path = os.path.join(self._data_path, 'Images', index + ext)
            if os.path.exists(image_path):
                break
        assert os.path.exists(image_path), \
                'Path does not exist: {}'.format(image_path)
        return image_path

    def _load_image_set_index(self):
        """
        Load the indexes listed in this dataset's image set file.
        """
        # Example path to image set file:
        # self._data_path + /ImageSets/val.txt
        image_set_file = os.path.join(self._data_path, 'ImageSets',
                                      self._image_set + '.txt')
        assert os.path.exists(image_set_file), \
                'Path does not exist: {}'.format(image_set_file)
        with open(image_set_file) as f:
            image_index = [x.strip() for x in f.readlines()]
        print ("Files found in ImageSet: %d" % len(image_index))
        filtered_image_index = []
        for ind in image_index:
            # Load and verify if the annotation file contains atleast one instance of the objet of interest
            filename = os.path.join(self._data_path, 'Annotations', ind + '.xml')
            with open(filename) as fd:
                doc = xmltodict.parse(fd.read())
            for xmlObjectName, xmlObjectData in doc['annotation'].iteritems():
                if xmlObjectName == 'object':
                    filtered_image_index.append(ind)
        # return image_index
        print ("Files left after filtering: %d" % len(filtered_image_index))
        return filtered_image_index

    def gt_roidb(self):
        """
        Return the database of ground-truth regions of interest.
        This function loads/saves from/to a cache file to speed up future calls.
        """
        cache_file = os.path.join(self.cache_path, self.name + '_gt_roidb.pkl')
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as fid:
                roidb = cPickle.load(fid)
            print '{} gt roidb loaded from {}'.format(self.name, cache_file)
            return roidb

        gt_roidb = [self._load_fishclef_annotation(index)
                    for index in self.image_index]
        with open(cache_file, 'wb') as fid:
            cPickle.dump(gt_roidb, fid, cPickle.HIGHEST_PROTOCOL)
        print 'wrote gt roidb to {}'.format(cache_file)

        return gt_roidb

    def rpn_roidb(self):
        gt_roidb = self.gt_roidb()
        rpn_roidb = self._load_rpn_roidb(gt_roidb)
        roidb = imdb.merge_roidbs(gt_roidb, rpn_roidb)
        #roidb = self._load_rpn_roidb(None)
        return roidb

    def _load_rpn_roidb(self, gt_roidb):
        filename = self.config['rpn_file']
        print 'loading {}'.format(filename)
        assert os.path.exists(filename), \
               'rpn data not found at: {}'.format(filename)
        with open(filename, 'rb') as f:
            box_list = cPickle.load(f)
        return self.create_roidb_from_box_list(box_list, gt_roidb)

    def _load_fishclef_annotation(self, index):
        """
        Load image and bounding boxes info from txt files of Table dataset.
        """
        filename = os.path.join(self._data_path, 'Annotations', index + '.xml')
        if USE_ORIGINAL_IMAGES:
            image_filename = os.path.join(self._data_path, 'Images', index + '-orig.png') # Added -orig
        else:
            image_filename = os.path.join(self._data_path, 'Images', index + '.png')
        with open(filename) as fd:
            doc = xmltodict.parse(fd.read())

        img = cv2.imread(image_filename)
        img_shape = img.shape

        # Get bboxes
        # num_objs = 3 # Needs to be changed for dynamism
        # boxes = np.zeros((num_objs, 4), dtype=np.uint16)
        # gt_classes = np.zeros((num_objs), dtype=np.int32)
        # overlaps = np.zeros((num_objs, self.num_classes), dtype=np.float32)
        # "Seg" area here is just the box area
        # seg_areas = np.zeros((num_objs), dtype=np.float32)

        boxes = []
        gt_classes = []
        overlaps = []
        seg_areas = []

        # Load object bounding boxes into a data frame.
        # for xmlObject in doc['annotation']['object']:
        for xmlObjectName, xmlObjectData in doc['annotation'].iteritems():
            if xmlObjectName == 'object':
                # print(xmlObjectData)
                if isinstance(xmlObjectData, list):
                    for xmlObject in xmlObjectData:
                        if xmlObject['name'].lower() in self._classes:
                            className = xmlObject['name'].lower()
                        else:
                            className = 'other'

                        # Make pixel indexes 0-based
                        x1 = float(xmlObject['bndbox']['xmin']) - 1
                        y1 = float(xmlObject['bndbox']['ymin']) - 1
                        x2 = float(xmlObject['bndbox']['xmax']) - 1
                        y2 = float(xmlObject['bndbox']['ymax']) - 1
                        if x1 < 0:
                            x1 = 0
                        if y1 < 0:
                            y1 = 0
                        if x2 >= img_shape[1]:
                            x2 = img_shape[1] - 1
                        if y2 >= img_shape[0]:
                            y2 = img_shape[0] - 1
                        cls = self._class_to_ind[className]
                        # print("%d %d %d %d" % (x1, y1, x2, y2))
                        boxes.append([x1, y1, x2, y2])
                        gt_classes.append(cls)
                        oneHotVec = np.zeros([self.num_classes], dtype=np.float32)
                        oneHotVec[cls] = 1.0
                        overlaps.append(oneHotVec)
                        seg_areas.append((x2 - x1 + 1) * (y2 - y1 + 1))
                else:
                    xmlObject = xmlObjectData
                    if xmlObject['name'].lower() in self._classes:
                        className = xmlObject['name'].lower()
                    else:
                        className = 'other'

                    # Make pixel indexes 0-based
                    x1 = float(xmlObject['bndbox']['xmin']) - 1
                    y1 = float(xmlObject['bndbox']['ymin']) - 1
                    x2 = float(xmlObject['bndbox']['xmax']) - 1
                    y2 = float(xmlObject['bndbox']['ymax']) - 1
                    if x1 < 0:
                        x1 = 0
                    if y1 < 0:
                        y1 = 0
                    if x2 >= img_shape[1]:
                        x2 = img_shape[1] - 1
                    if y2 >= img_shape[0]:
                        y2 = img_shape[0] - 1
                    cls = self._class_to_ind[className]
                    # print("%d %d %d %d" % (x1, y1, x2, y2))
                    boxes.append([x1, y1, x2, y2])
                    gt_classes.append(cls)
                    oneHotVec = np.zeros([self.num_classes], dtype=np.float32)
                    oneHotVec[cls] = 1.0
                    overlaps.append(oneHotVec)
                    seg_areas.append((x2 - x1 + 1) * (y2 - y1 + 1))

        boxes = np.array(boxes, dtype=np.uint16)
        gt_classes = np.array(gt_classes, dtype=np.int32)
        overlaps = np.array(overlaps, dtype=np.float32)
        seg_areas = np.array(seg_areas, dtype=np.float32)

        overlaps = scipy.sparse.csr_matrix(overlaps)

        return {'boxes' : boxes,
                'gt_classes': gt_classes,
                'gt_overlaps' : overlaps,
                'flipped' : False,
                'seg_areas' : seg_areas}

    def _write_fishclef_results_file(self, all_boxes):
        for cls_ind, cls in enumerate(self.classes):
            if cls == '__background__':
                continue
            print 'Writing {} results file'.format(cls)
            filename = self._get_fishclef_results_file_template().format(cls)
            with open(filename, 'wt') as f:
                for im_ind, index in enumerate(self.image_index):
                    dets = all_boxes[cls_ind][im_ind]
                    if dets == []:
                        continue
                    # the VOCdevkit expects 1-based indices
                    for k in xrange(dets.shape[0]):
                        f.write('{:s} {:.3f} {:.1f} {:.1f} {:.1f} {:.1f}\n'.
                                format(index, dets[k, -1],
                                       dets[k, 0] + 1, dets[k, 1] + 1,
                                       dets[k, 2] + 1, dets[k, 3] + 1))

    def evaluate_detections(self, all_boxes, output_dir):
        self._write_fishclef_results_file(all_boxes)
        self._do_python_eval(output_dir)
        if self.config['cleanup']:
            for cls in self._classes:
                if cls == '__background__':
                    continue
                filename = self._get_fishclef_results_file_template().format(cls)
                os.remove(filename)

    def _get_comp_id(self):
        comp_id = (self._comp_id + '_' + self._salt if self.config['use_salt']
            else self._comp_id)
        return comp_id

    def _get_fishclef_results_file_template(self):
        # INRIAdevkit/results/comp4-44503_det_test_{%s}.txt
        filename = self._get_comp_id() + '_det_' + self._image_set + '_{:s}.txt'
        try:
            os.mkdir(self._devkit_path + '/results')
        except OSError as e:
            if e.errno == errno.EEXIST:
                pass
            else:
                raise e
        path = os.path.join(
            self._devkit_path,
            'results',
            filename)
        return path

    def _do_python_eval(self, output_dir = 'output'):
        annopath = os.path.join(
            self._data_path,
            'Annotations',
            '{:s}.txt')
        imagesetfile = os.path.join(
            self._data_path,
            'ImageSets',
            self._image_set + '.txt')
        cachedir = os.path.join(self._devkit_path, 'annotations_cache')
        aps = []
        if not os.path.isdir(output_dir):
            os.mkdir(output_dir)
        for i, cls in enumerate(self._classes):
            if cls == '__background__':
                continue
            filename = self._get_fishclef_results_file_template().format(cls)
            rec, prec, ap = fishclef_eval(
                filename, annopath, imagesetfile, cls, cachedir, ovthresh=0.5)
            aps += [ap]
            print('AP for {} = {:.4f}'.format(cls, ap))
            with open(os.path.join(output_dir, cls + '_pr.pkl'), 'w') as f:
                cPickle.dump({'rec': rec, 'prec': prec, 'ap': ap}, f)
        print('Mean AP = {:.4f}'.format(np.mean(aps)))
        print('~~~~~~~~')
        print('Results:')
        for ap in aps:
            print('{:.3f}'.format(ap))
        print('{:.3f}'.format(np.mean(aps)))
        print('~~~~~~~~')
        print('')
        print('--------------------------------------------------------------')
        print('Results computed with the **unofficial** Python eval code.')
        print('Results should be very close to the official MATLAB eval code.')
        print('Recompute with `./tools/reval.py --matlab ...` for your paper.')
        print('-- Thanks, The Management')
        print('--------------------------------------------------------------')
