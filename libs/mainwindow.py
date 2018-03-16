#!/usr/bin/env python
# -*- coding: utf-8 -*-
import codecs
import datetime
import json
import lmdb
import os
import os.path
import re
import resources
import subprocess
import sys

try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    # needed for py3+qt4
    # Ref:
    # http://pyqt.sourceforge.net/Docs/PyQt4/incompatible_apis.html
    # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string
    if sys.version_info.major >= 3:
        import sip
        sip.setapi('QVariant', 2)
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

# Add internal libs
from bisect import insort
from collections import defaultdict
from functools import partial
from libs.canvas import Canvas
from libs.colorDialog import ColorDialog
from libs.constants import *
from libs.labelDialog import LabelDialog
from libs.labelFile import LabelFile, LabelFileError
from libs.lib import struct, newAction, newIcon, addActions, fmtShortcut, generateColorByText
from libs.loginDialog import Login
from libs.pascal_voc_io import PascalVocReader, XML_EXT
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.toolBar import ToolBar
from libs.ustr import ustr
from libs.version import __version__
from libs.zoomWidget import ZoomWidget

__appname__ = 'vanno_ver' if sys.argv[0].split('/')[-1] == 'vanno_ver.py' else 'vanno'
dataset = 'jester'
env_path = '../vanno_results/' + dataset + '_env'
results_path = '../vanno_results/' + dataset

# Utility functions and classes.
def have_qstring():
    '''p3/qt5 get rid of QString wrapper as py3 has native unicode str type'''
    return not (sys.version_info.major >= 3 or QT_VERSION_STR.startswith('5.'))


def util_qt_strlistclass():
    return QStringList if have_qstring() else list


class WindowMixin(object):
    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            addActions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


# PyQt5: TypeError: unhashable type: 'QListWidgetItem'
class HashableQListWidgetItem(QListWidgetItem):
    def __init__(self, *args):
        super(HashableQListWidgetItem, self).__init__(*args)

    def __hash__(self):
        return hash(id(self))


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(self,logged_id, defaultFilename=None, defaultPrefdefClassFile=None):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()
        settings = self.settings

        # Save as Pascal voc xml
        self.defaultSaveDir = '../vanno_results/' + dataset     ###
        self.defaultSaveDir_folder= ''
        self.usingPascalVocFormat = True
        # For loading all image under a directory
        self.mImgList = []
        self.job_list_per_sess = []
        self.dirname = None
        self.labelHist = []
        self.lastOpenDir = None
        self.old_Filepath=None
        # self.proj_dir=None
        self.imageDirPath = None
        self.imageDirPath_folder = ''
        ###
        self.job_list_dict = dict()
        self.job_list = []
        self.sess_no = 0

        # Whether we need to save or not.
        self.dirty = False

        self._noSelectionSlot = False
        self._beginner = True
        self.screencastViewer = "firefox"
        self.screencast = "https://youtu.be/p0nR2YsCY_U"

        self.logged_id = logged_id

        self.ids = []
        # with open('./env/ids.txt', 'r') as f:
        #     lines = f.readlines()
        #     for line in lines:
        #         self.ids.append(line.replace('\n', ''))
        file = QFile('./env/ids.txt')
        if file.open(QFile.ReadOnly | QFile.Text):
            while(not file.atEnd()):
                line = bytearray(file.readLine()).decode().strip()
                self.ids.append(line)
        file.close()

        # Load predefined classes to the list
        if defaultPrefdefClassFile is not None:
            self.loadPredefinedClasses(defaultPrefdefClassFile)

        # Main widgets and related state.
        self.labelDialog = LabelDialog(parent=self, listItem=self.labelHist)

        self.itemsToShapes = {}
        self.shapesToItems = {}
        self.prevLabelText = ''

        listLayout = QVBoxLayout()
        listLayout.setContentsMargins(0, 0, 0, 0)

        # Create a widget for using default label
        self.useDefaultLabelCheckbox = QCheckBox(u'Use default label')
        self.useDefaultLabelCheckbox.setChecked(True)###
        self.defaultLabelTextLine = QLineEdit()
        ###
        self.defaultLabelTextLine.setText('hand')
        useDefaultLabelQHBoxLayout = QHBoxLayout()
        useDefaultLabelQHBoxLayout.addWidget(self.useDefaultLabelCheckbox)
        useDefaultLabelQHBoxLayout.addWidget(self.defaultLabelTextLine)
        useDefaultLabelContainer = QWidget()
        useDefaultLabelContainer.setLayout(useDefaultLabelQHBoxLayout)

        # Create a widget for edit and diffc button
        self.diffcButton = QCheckBox(u'difficult')
        self.diffcButton.setChecked(False)
        self.diffcButton.stateChanged.connect(self.btnstate)
        self.editButton = QToolButton()
        self.editButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        self.start_label = QLabel()
        self.start_label.setText('Start: ')
        self.start_label.setFixedWidth(130)
        self.end_label = QLabel()
        self.end_label.setText('End: ')
        self.end_label.setFixedWidth(130)

        self.edit_label = QLabel()
        self.save_label = QLabel()
        self.anno_label = QLabel()
        self.anno_label.setText('XML: ')
        self.id_label = QLabel()

        ###
        self.edit_label.setText('Image DIR: ')
        self.save_label.setText('Save DIR: ' + self.defaultSaveDir)

        # Add some of widgets to listLayout
        listLayout.addWidget(self.editButton)
        listLayout.addWidget(self.diffcButton)
        listLayout.addWidget(useDefaultLabelContainer)

        # Create and add a widget for showing current label items
        self.labelList = QListWidget()
        labelListContainer = QWidget()
        labelListContainer.setLayout(listLayout)
        self.labelList.itemActivated.connect(self.labelSelectionChanged)
        self.labelList.itemSelectionChanged.connect(self.labelSelectionChanged)
        self.labelList.itemDoubleClicked.connect(self.editLabel)
        # Connect to itemChanged to detect checkbox changes.
        self.labelList.itemChanged.connect(self.labelItemChanged)
        listLayout.addWidget(self.labelList)

        ###
        self.sessLabel = QLabel()
        self.sessLabel.setText('Input session number.')
        listLayout.addWidget(self.sessLabel)

        self.curSession = 1
        self.curSessLineEdit = QLineEdit()
        self.curSessLineEdit.setFixedWidth(40)
        self.curSessLineEdit.returnPressed.connect(self.importDirs)

        ###
        self.prevSessBtn = QToolButton()
        self.prevSessBtn.setArrowType(Qt.LeftArrow)
        self.prevSessBtn.clicked.connect(self.prevSess)
        self.nextSessBtn = QToolButton()
        self.nextSessBtn.setArrowType(Qt.RightArrow)
        self.nextSessBtn.clicked.connect(self.nextSess)

        hbox = QHBoxLayout()
        # hbox.addStretch()
        hbox.addWidget(self.prevSessBtn)
        hbox.addWidget(self.curSessLineEdit)
        hbox.addWidget(self.nextSessBtn)
        listLayout.addLayout(hbox)

        hbox.addWidget(self.start_label)
        hbox.addWidget(self.end_label)

        listLayout.addWidget(self.anno_label)
        listLayout.addWidget(self.edit_label)
        listLayout.addWidget(self.save_label)

        #
        self.dock = QDockWidget('ID: ' + self.logged_id, self)
        self.dock.setObjectName(u'Labels')
        self.dock.setWidget(labelListContainer)

        self.folderListWidget = QListWidget()
        self.folderListWidget.itemDoubleClicked.connect(self.diritemDoubleClicked)
        self.folderListWidget.itemChanged.connect(self.diritemChanged)

        folderlistLayout = QVBoxLayout()
        folderlistLayout.setContentsMargins(0, 0, 0, 0)

        ###
        self.savebtncnt_label = QLabel()
        folderlistLayout.addWidget(self.savebtncnt_label)
        self.savebtn_label = QLabel()
        self.savebtn_label.setStyleSheet('color: red')
        folderlistLayout.addWidget(self.savebtn_label)

        ####
        if self.logged_id != 'vdo_ver':
            self.saveButton = QToolButton()
            self.saveButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.saveButton.setText('Save finished folders')
            self.saveButton.clicked.connect(self.saveButtonClicked)
            folderlistLayout.addWidget(self.saveButton)

        folderlistLayout.addWidget(self.folderListWidget)
        folderListContainer = QWidget()
        folderListContainer.setLayout(folderlistLayout)
        self.folderdock = QDockWidget(u'Folder List', self)
        self.folderdock.setObjectName(u'Folders')
        self.folderdock.setWidget(folderListContainer)

        # Tzutalin 20160906 : Add file list and dock to move faster
        self.fileListWidget = QListWidget()
        self.fileListWidget.itemDoubleClicked.connect(self.fileitemDoubleClicked)
        filelistLayout = QVBoxLayout()
        filelistLayout.setContentsMargins(0, 0, 0, 0)
        filelistLayout.addWidget(self.fileListWidget)
        fileListContainer = QWidget()
        fileListContainer.setLayout(filelistLayout)
        self.filedock = QDockWidget(u'File List', self)
        self.filedock.setObjectName(u'Files')
        self.filedock.setWidget(fileListContainer)

        self.zoomWidget = ZoomWidget()
        self.colorDialog = ColorDialog(parent=self)

        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoomRequest)

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scrollBars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar()
        }
        self.scrollArea = scroll
        self.canvas.scrollRequest.connect(self.scrollRequest)

        self.canvas.newShape.connect(self.newShape)
        self.canvas.shapeMoved.connect(self.setDirty)
        self.canvas.selectionChanged.connect(self.shapeSelectionChanged)
        self.canvas.drawingPolygon.connect(self.toggleDrawingSensitive)

        self.setCentralWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        # Tzutalin 20160906 : Add file list and dock to move faster


        self.addDockWidget(Qt.RightDockWidgetArea, self.folderdock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.filedock)
        # self.filedock.setFeatures(QDockWidget.DockWidgetFloatable)

        # self.dockFeatures = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        # self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)

        ###
        self.n_folder = 0
        self.checkList = []
        self.start_img_file = ''
        self.end_img_file = ''
        ####
        self.lmdb = lmdb.open(os.path.join(env_path, self.logged_id))
        self.checknum = 0
        self.job_list_total = []
        self.job_list_total_no = 0

        # Actions
        action = partial(newAction, self)
        ###
        StartImg = action('Start Image', self.set_start, 'q', 'start', 'Set as start image')

        EndImg = action('End Image', self.set_end, 'e', 'end', 'Set as end image')

        quit = action('&Quit', self.close,
                      'Ctrl+Q', 'quit', u'Quit application')

        open = action('&Open', self.openFile,
                      'Ctrl+O', 'open', u'Open image or label file')

        opendir = action('&Open Dir', self.openDirDialog,
                         'u', 'open', u'Open Dir')

        changeSavedir = action('&Change Save Dir', self.changeSavedirDialog,
                               'r', 'open', u'Change default saved Annotation dir')

        openAnnotation = action('&Open Annotation', self.openAnnotationDialog,
                                'Ctrl+Shift+O', 'open', u'Open Annotation')

        openNextImg = action('&Next Image', self.openNextImg,
                             'd', 'next', u'Open Next')

        openPrevImg = action('&Prev Image', self.openPrevImg,
                             'a', 'prev', u'Open Prev')
        ####
        if self.logged_id == 'vdo_ver':
            verify = action('&Verify Image', self.verifyImg,
                            'space', 'verify', u'Verify Image')

        # toggle_difficult = action()    #####

        save = action('&Save', self.saveFile,
                      's', 'save', u'Save labels to file', enabled=False)

        saveAs = action('&Save As', self.saveFileAs,
                        'Ctrl+Shift+S', 'save-as', u'Save labels to a different file', enabled=False)

        close = action('&Close', self.closeFile, 'Ctrl+W', 'close', u'Close current file')

        resetAll = action('&ResetAll', self.resetAll, None, 'resetall', u'Reset all')

        color1 = action('Box Line Color', self.chooseColor1,
                        'Ctrl+L', 'color_line', u'Choose Box line color')

        createMode = action('Create\nRectBox', self.setCreateMode,
                            'w', 'new', u'Start drawing Boxs', enabled=False)
        editMode = action('&Edit\nRectBox', self.setEditMode,
                          'Ctrl+J', 'edit', u'Move and edit Boxs', enabled=False)

        create = action('Create\nRectBox', self.createShape,
                        'w', 'new', u'Draw a new Box', enabled=False)
        delete = action('Delete\nRectBox', self.deleteSelectedShape,
                        'Delete', 'delete', u'Delete', enabled=False)
        copy = action('&Duplicate\nRectBox', self.copySelectedShape,
                      'Ctrl+D', 'copy', u'Create a duplicate of the selected Box',
                      enabled=False)

        advancedMode = action('&Advanced Mode', self.toggleAdvancedMode,
                              'Ctrl+Shift+A', 'expert', u'Switch to advanced mode',
                              checkable=True)

        hideAll = action('&Hide\nRectBox', partial(self.togglePolygons, False),
                         'Ctrl+H', 'hide', u'Hide all Boxs',
                         enabled=False)
        showAll = action('&Show\nRectBox', partial(self.togglePolygons, True),
                         'Ctrl+A', 'hide', u'Show all Boxs',
                         enabled=False)

        help = action('&Tutorial', self.showTutorialDialog, None, 'help', u'Show demos')
        showInfo = action('&Information', self.showInfoDialog, None, 'help', u'Information')

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoomWidget)
        self.zoomWidget.setWhatsThis(
            u"Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas." % (fmtShortcut("Ctrl+[-+]"),
                                             fmtShortcut("Ctrl+Wheel")))
        self.zoomWidget.setEnabled(False)

        zoomIn = action('Zoom &In', partial(self.addZoom, 10),
                        'Ctrl++', 'zoom-in', u'Increase zoom level', enabled=False)
        zoomOut = action('&Zoom Out', partial(self.addZoom, -10),
                         'Ctrl+-', 'zoom-out', u'Decrease zoom level', enabled=False)
        zoomOrg = action('&Original size', partial(self.setZoom, 100),
                         'Ctrl+=', 'zoom', u'Zoom to original size', enabled=False)
        fitWindow = action('&Fit Window', self.setFitWindow,
                           'Ctrl+F', 'fit-window', u'Zoom follows window size',
                           checkable=True, enabled=False)
        fitWidth = action('Fit &Width', self.setFitWidth,
                          'Ctrl+Shift+F', 'fit-width', u'Zoom follows window width',
                          checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoomActions = (self.zoomWidget, zoomIn, zoomOut,
                       zoomOrg, fitWindow, fitWidth)
        self.zoomMode = self.MANUAL_ZOOM
        self.scalers = {
            self.FIT_WINDOW: self.scaleFitWindow,
            self.FIT_WIDTH: self.scaleFitWidth,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action('&Edit Label', self.editLabel,
                      'Ctrl+E', 'edit', u'Modify the label of the selected Box',
                      enabled=False)
        self.editButton.setDefaultAction(edit)

        shapeLineColor = action('Shape &Line Color', self.chshapeLineColor,
                                icon='color_line', tip=u'Change the line color for this specific shape',
                                enabled=False)
        shapeFillColor = action('Shape &Fill Color', self.chshapeFillColor,
                                icon='color', tip=u'Change the fill color for this specific shape',
                                enabled=False)

        labels = self.dock.toggleViewAction()
        labels.setText('Show/Hide Label Panel')
        labels.setShortcut('Ctrl+Shift+L')

        # Lavel list context menu.
        labelMenu = QMenu()
        addActions(labelMenu, (edit, delete))
        self.labelList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.labelList.customContextMenuRequested.connect(
            self.popLabelListMenu)

        # Store actions for further handling.
        self.actions = struct(save=save, saveAs=saveAs, open=open, close=close, resetAll=resetAll,
                              lineColor=color1, create=create, delete=delete, edit=edit, copy=copy,
                              createMode=createMode, editMode=editMode, advancedMode=advancedMode,
                              shapeLineColor=shapeLineColor, shapeFillColor=shapeFillColor,
                              zoom=zoom, zoomIn=zoomIn, zoomOut=zoomOut, zoomOrg=zoomOrg,
                              fitWindow=fitWindow, fitWidth=fitWidth,
                              zoomActions=zoomActions,
                              fileMenuActions=(
                                  open, opendir, save, saveAs, close, resetAll, quit),
                              beginner=(), advanced=(),
                              editMenu=(edit, copy, delete,
                                        None, color1),
                              beginnerContext=(create, edit, copy, delete),
                              advancedContext=(createMode, editMode, edit, copy,
                                               delete, shapeLineColor, shapeFillColor),
                              onLoadActive=(
                                  close, create, createMode, editMode),
                              onShapesPresent=(saveAs, hideAll, showAll))

        self.menus = struct(
            file=self.menu('&File'),
            edit=self.menu('&Edit'),
            view=self.menu('&View'),
            help=self.menu('&Help'),
            recentFiles=QMenu('Open &Recent'),
            labelList=labelMenu)

        # Auto saving : Enable auto saving if pressing next
        self.autoSaving = QAction("Auto Saving", self)
        self.autoSaving.setCheckable(True)
        self.autoSaving.setChecked(settings.get(SETTING_AUTO_SAVE, False))
        # Sync single class mode from PR#106
        self.singleClassMode = QAction("Single Class Mode", self)
        self.singleClassMode.setShortcut("Ctrl+Shift+S")
        self.singleClassMode.setCheckable(True)
        self.singleClassMode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))
        self.lastLabel = None

        addActions(self.menus.file,
                   (StartImg, EndImg, open, opendir, changeSavedir, openAnnotation, self.menus.recentFiles, save, saveAs, close, resetAll, quit))
        addActions(self.menus.help, (help, showInfo))
        addActions(self.menus.view, (
            self.autoSaving,
            self.singleClassMode,
            labels, advancedMode, None,
            hideAll, showAll, None,
            zoomIn, zoomOut, zoomOrg, None,
            fitWindow, fitWidth))

        self.menus.file.aboutToShow.connect(self.updateFileMenu)

        # Custom context menu for the canvas widget:
        addActions(self.canvas.menus[0], self.actions.beginnerContext)
        addActions(self.canvas.menus[1], (
            action('&Copy here', self.copyShape),
            action('&Move here', self.moveShape)))

        self.tools = self.toolbar('Tools')
        ####
        if self.logged_id == 'vdo_ver':
            self.actions.beginner = (
                open, opendir, changeSavedir, openNextImg, openPrevImg, verify, save, None, create, copy, delete, None,
                zoomIn, zoom, zoomOut, fitWindow, fitWidth)
        else:
            self.actions.beginner = (
                open, opendir, changeSavedir, openNextImg, openPrevImg, save, None, create, copy, delete, None,
                zoomIn, zoom, zoomOut, fitWindow, fitWidth)

        self.actions.advanced = (
            open, opendir, changeSavedir, openNextImg, openPrevImg, save, None,
            createMode, editMode, None,
            hideAll, showAll)

        self.statusBar().showMessage('%s started.' % __appname__)
        self.statusBar().show()

        # Application state.
        self.image = QImage()
        self.filePath = ustr(defaultFilename)
        self.recentFiles = []
        self.maxRecent = 7
        self.lineColor = None
        self.fillColor = None
        self.zoom_level = 100
        self.fit_window = False
        # Add Chris
        self.difficult = False

        ## Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recentFileQStringList = settings.get(SETTING_RECENT_FILES)
                self.recentFiles = [ustr(i) for i in recentFileQStringList]
            else:
                self.recentFiles = recentFileQStringList = settings.get(SETTING_RECENT_FILES)

        size = settings.get(SETTING_WIN_SIZE, QSize(600, 500))
        position = settings.get(SETTING_WIN_POSE, QPoint(0, 0))
        self.resize(size)
        self.move(position)
        saveDir = ustr(settings.get(SETTING_SAVE_DIR, None))
        self.lastOpenDir = ustr(settings.get(SETTING_LAST_OPEN_DIR, None))
        # if saveDir is not None and os.path.exists(saveDir):
        #     self.defaultSaveDir = saveDir
        #     self.statusBar().showMessage('%s started. Annotation will be saved to %s' %
        #                                  (__appname__, self.defaultSaveDir))
        #     self.statusBar().show()

        # self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        Shape.line_color = self.lineColor = QColor(settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR))
        Shape.fill_color = self.fillColor = QColor(settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR))
        self.canvas.setDrawingColor(self.lineColor)
        # Add chris
        Shape.difficult = self.difficult

        def xbool(x):
            if isinstance(x, QVariant):
                return x.toBool()
            return bool(x)

        if xbool(settings.get(SETTING_ADVANCE_MODE, False)):
            self.actions.advancedMode.setChecked(True)
            self.toggleAdvancedMode()

        # Populate the File menu dynamically.
        self.updateFileMenu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.filePath and os.path.isdir(self.filePath):
            self.queueEvent(partial(self.importDirImages, self.filePath or ""))
        elif self.filePath:
            self.queueEvent(partial(self.loadFile, self.filePath or ""))

        # Callbacks:
        self.zoomWidget.valueChanged.connect(self.paintCanvas)

        self.populateModeActions()

        # Display cursor coordinates at the right of status bar
        self.labelCoordinates = QLabel('')
        self.statusBar().addPermanentWidget(self.labelCoordinates)

        # Open Dir if default file
        if self.filePath and os.path.isdir(self.filePath):
            self.openDirDialog(dirpath=self.filePath)


    ## Support Functions ##
    def addLabel(self, shape):
        item = HashableQListWidgetItem(shape.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setBackground(generateColorByText(shape.label))
        self.itemsToShapes[item] = shape
        self.shapesToItems[shape] = item
        self.labelList.addItem(item)

        self.canvas.itemsToShapes[item] = shape
        self.canvas.shapesToItems[shape] = item
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)


    def addRecentFile(self, filePath):
        if filePath in self.recentFiles:
            self.recentFiles.remove(filePath)
        elif len(self.recentFiles) >= self.maxRecent:
            self.recentFiles.pop()
        self.recentFiles.insert(0, filePath)


    def addZoom(self, increment=10):
        self.setZoom(self.zoomWidget.value() + increment)


    def adjustScale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoomMode]()
        self.zoomWidget.setValue(int(100 * value))


    def advanced(self):
        return not self.beginner()


    def beginner(self):
        return self._beginner


    # Add chris
    def btnstate(self, item= None):
        """ Function to handle difficult examples
        Update on each object """
        if not self.canvas.editing():
            return

        item = self.currentItem()
        if not item: # If not selected Item, take the first one
            item = self.labelList.item(self.labelList.count()-1)

        difficult = self.diffcButton.isChecked()

        try:
            shape = self.itemsToShapes[item]
        except:
            pass
        # Checked and Update
        try:
            if difficult != shape.difficult:
                shape.difficult = difficult
                self.setDirty()
            else:  # User probably changed item visibility
                self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)
        except:
            pass


    def changeSavedirDialog(self, _value=False):
        if self.defaultSaveDir is not None:
            path = ustr(self.defaultSaveDir)
        else:
            path = '.'

        dirpath = ustr(QFileDialog.getExistingDirectory(self,
                                                       '%s - Save annotations to the directory' % __appname__, path,  QFileDialog.ShowDirsOnly
                                                       | QFileDialog.DontResolveSymlinks))
        if dirpath is not None and len(dirpath) > 1:
            self.defaultSaveDir = dirpath
            self.save_label.setText("Save DIR: " + dirpath)

        self.statusBar().showMessage('%s . Annotation will be saved to %s' %
                                     ('Change saved folder', self.defaultSaveDir))
        self.statusBar().show()


    def chooseColor1(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.lineColor = color
            Shape.line_color = color
            self.canvas.setDrawingColor(color)
            self.canvas.update()
            self.setDirty()


    def chshapeFillColor(self):
        color = self.colorDialog.getColor(self.fillColor, u'Choose fill color',
                                          default=DEFAULT_FILL_COLOR)
        if color:
            self.canvas.selectedShape.fill_color = color
            self.canvas.update()
            self.setDirty()


    def chshapeLineColor(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.canvas.selectedShape.line_color = color
            self.canvas.update()
            self.setDirty()

    ###
    def closeEvent(self, event):
        if self.savebtn_label.text() == 'Not saved':
            QMessageBox.warning(self, 'Warning', 'You forgot to press "Save finished folders" button.')

        if not self.mayContinue():
            event.ignore()
        settings = self.settings
        # If it loads images from dir, don't load it at the begining
        if self.dirname is None:
            settings[SETTING_FILENAME] = self.filePath if self.filePath else ''
        else:
            settings[SETTING_FILENAME] = ''

        settings[SETTING_WIN_SIZE] = self.size()
        settings[SETTING_WIN_POSE] = self.pos()
        settings[SETTING_WIN_STATE] = self.saveState()
        settings[SETTING_LINE_COLOR] = self.lineColor
        settings[SETTING_FILL_COLOR] = self.fillColor
        settings[SETTING_RECENT_FILES] = self.recentFiles
        settings[SETTING_ADVANCE_MODE] = not self._beginner
        if self.defaultSaveDir and os.path.exists(self.defaultSaveDir):
            settings[SETTING_SAVE_DIR] = ustr(self.defaultSaveDir)
        else:
            settings[SETTING_SAVE_DIR] = ""

        if self.lastOpenDir and os.path.exists(self.lastOpenDir):
            settings[SETTING_LAST_OPEN_DIR] = self.lastOpenDir
        else:
            settings[SETTING_LAST_OPEN_DIR] = ""

        settings[SETTING_AUTO_SAVE] = self.autoSaving.isChecked()
        settings[SETTING_SINGLE_CLASS] = self.singleClassMode.isChecked()
        settings.save()


    def closeFile(self, _value=False):
        if self.savebtn_label.text() == 'Not saved':
            return QMessageBox.warning(self, 'Warning', 'Please press "Save finished folders" button.')
        if not self.mayContinue():
            return
        self.resetState()
        self.setClean()
        self.toggleActions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)


    def copySelectedShape(self):
        self.addLabel(self.canvas.copySelectedShape())
        # fix copy and delete
        self.shapeSelectionChanged(True)


    def copyShape(self):
        self.canvas.endMove(copy=True)
        self.addLabel(self.canvas.selectedShape)
        self.setDirty()


    def createShape(self):
        assert self.beginner()
        self.canvas.setEditing(False)
        self.actions.create.setEnabled(False)


    def currentItem(self):
        items = self.labelList.selectedItems()
        if items:
            return items[0]
        return None


    def currentPath(self):
        return os.path.dirname(self.filePath) if self.filePath else '.'


    def deleteSelectedShape(self):
        self.remLabel(self.canvas.deleteSelected())
        self.setDirty()
        if self.noShapes():
            for action in self.actions.onShapesPresent:
                action.setEnabled(False)

    ###
    def diritemChanged(self, item=None):
        # QMessageBox.warning(self, u'changed', msg, yes | no)
        self.savebtn_label.setText('Not saved')
        if int(item.text()) in self.checkList:
            self.checkList.remove(int(item.text()))
        else:
            insort(self.checkList, int(item.text()))
        self.savebtncnt_label.setText('{0}/{1}'.format(len(self.checkList), self.n_folder))


    def diritemDoubleClicked(self, item=None):
        currIndex = self.job_list_per_sess.index(ustr(item.text()))
        if currIndex < len(self.job_list_per_sess):
            foldername = self.job_list_per_sess[currIndex]
            if foldername:
                self.defaultSaveDir_folder = os.path.join(self.defaultSaveDir, foldername)
                self.imageDirPath_folder = os.path.join(self.defaultSaveDir, foldername)
                self.importDirImages(os.path.join(self.imageDirPath, foldername))
                self.save_label.setText('Save DIR: ' + self.imageDirPath_folder)
                self.fileListWidget.setFocus(True)


    def discardChangesDialog(self):
        yes, no = QMessageBox.Yes, QMessageBox.No
        msg = u'You have unsaved changes, proceed anyway?'
        return yes == QMessageBox.warning(self, u'Attention', msg, yes | no)


    def editLabel(self):
        if not self.canvas.editing():
            return
        item = self.currentItem()
        text = self.labelDialog.popUp(item.text())
        if text is not None:
            item.setText(text)
            item.setBackground(generateColorByText(text))
            self.setDirty()


    def errorMessage(self, title, message):
        return QMessageBox.critical(self, title,
                                    '<p><b>%s</b></p>%s' % (title, message))


    # Tzutalin 20160906 : Add file list and dock to move faster
    def fileitemDoubleClicked(self, item=None):
        currIndex = self.mImgList.index(ustr(item.text()))
        if currIndex < len(self.mImgList):
            filename = self.mImgList[currIndex]
            if filename:
                self.loadFile(filename)


    def importDirImages(self, dirpath):
        if not self.mayContinue() or not dirpath:
            return

        # self.lastOpenDir = dirpath
        self.dirname = dirpath
        self.filePath = None
        self.fileListWidget.clear()
        self.mImgList = self.scanAllImages(dirpath)
        self.openNextImg()
        self.fileListWidget.setFocus(True)
        for imgPath in self.mImgList:
            item = QListWidgetItem(imgPath)
            self.fileListWidget.addItem(item)
        self.edit_label.setText('Image DIR: ' + dirpath)

    ###
    def importDirs(self):
        if self.curSessLineEdit.text() == '':
            return

        if self.savebtn_label.text() == 'Not saved':
            return QMessageBox.warning(self, 'Warning', 'Please press "Save finished folders" button.')

        if not self.mayContinue():
            return

        self.checkList = []
        file = QFile(env_path + '/' + self.logged_id + '_' + str(int(self.curSessLineEdit.text())).zfill(2) + '.txt')
        if file.open(QFile.ReadOnly | QFile.Text):
            while not file.atEnd():
                line = int(bytearray(file.readLine()).decode().strip())
                insort(self.checkList, line)
        file.close()

        if int(self.curSessLineEdit.text()) > self.sess_no or int(self.curSessLineEdit.text()) <= 0:
            return QMessageBox.warning(self, 'Error', '<p><b>IndexError:</b></p>list index out of range')

        ###
        self.lastOpenDir = os.path.join(self.imageDirPath, str(self.curSession))
        self.curSession = int(self.curSessLineEdit.text())
        self.folderListWidget.clear()
        self.n_folder = 0
        self.job_list_per_sess = self.job_list[self.curSession - 1]
        for folder_path in self.job_list_per_sess:
            self.n_folder += 1
            item = QListWidgetItem(folder_path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            if int(item.text()) in self.checkList:
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.folderListWidget.addItem(item)

        self.savebtncnt_label.setText('{0}/{1}'.format(len(self.checkList), self.n_folder))
        self.edit_label.setText('Image DIR: ' + self.imageDirPath)


    def importJobs(self):
        return json.load(open(os.path.join(env_path, 'job_assign.json')))


    def labelItemChanged(self, item):
        shape = self.itemsToShapes[item]
        label = item.text()
        if label != shape.label:
            shape.label = item.text()
            shape.line_color = generateColorByText(shape.label)
            self.setDirty()
        else:  # User probably changed item visibility
            self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)


    def labelSelectionChanged(self):
        item = self.currentItem()
        if item and self.canvas.editing():
            self._noSelectionSlot = True
            self.canvas.selectShape(self.itemsToShapes[item])
            shape = self.itemsToShapes[item]
            # Add Chris
            self.diffcButton.setChecked(shape.difficult)


    def loadFile(self, filePath=None):
        """Load the specified file, or the last opened file if None."""
        self.resetState()
        self.canvas.setEnabled(False)
        if filePath is None:
            filePath = self.settings.get(SETTING_FILENAME)

        # Make sure that filePath is a regular python string, rather than QString
        filePath = str(filePath)

        unicodeFilePath = ustr(filePath)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if unicodeFilePath and self.fileListWidget.count() > 0:
            index = self.mImgList.index(unicodeFilePath)
            fileWidgetItem = self.fileListWidget.item(index)
            fileWidgetItem.setSelected(True)

        if unicodeFilePath and os.path.exists(unicodeFilePath):
            if LabelFile.isLabelFile(unicodeFilePath):
                try:
                    self.labelFile = LabelFile(unicodeFilePath)
                except LabelFileError as e:
                    self.errorMessage(u'Error opening file',
                                      (u"<p><b>%s</b></p>"
                                       u"<p>Make sure <i>%s</i> is a valid label file.")
                                      % (e, unicodeFilePath))
                    self.status("Error reading %s" % unicodeFilePath)
                    return False
                self.imageData = self.labelFile.imageData
                self.lineColor = QColor(*self.labelFile.lineColor)
                self.fillColor = QColor(*self.labelFile.fillColor)
            else:
                # Load image:
                # read data first and store for saving into label file.
                self.imageData = read(unicodeFilePath, None)
                self.labelFile = None

            image = QImage.fromData(self.imageData)
            if image.isNull():
                self.errorMessage(u'Error opening file',
                                  u"<p>Make sure <i>%s</i> is a valid image file." % unicodeFilePath)
                self.status("Error reading %s" % unicodeFilePath)
                return False
            self.status("Loaded %s" % os.path.basename(unicodeFilePath))
            self.image = image
            self.filePath = unicodeFilePath
            self.canvas.loadPixmap(QPixmap.fromImage(image))
            if self.labelFile:
                self.loadLabels(self.labelFile.shapes)
            self.setClean()
            self.canvas.setEnabled(True)
            self.adjustScale(initial=True)
            self.paintCanvas()
            self.addRecentFile(self.filePath)
            self.toggleActions(True)
            bsuccess = True

            # Label xml file and show bound box according to its filename
            if self.usingPascalVocFormat is True:
                if self.defaultSaveDir_folder is not None:
                    basename = os.path.basename(
                        os.path.splitext(self.filePath)[0]) + XML_EXT
                    xmlPath = os.path.join(self.defaultSaveDir_folder, basename)
                    bsuccess = self.loadPascalXMLByFilename(xmlPath)
                else:
                    xmlPath = os.path.splitext(filePath)[0] + XML_EXT
                    if os.path.isfile(xmlPath):
                        bsuccess = self.loadPascalXMLByFilename(xmlPath)

                ###
                self.old_Filepath = str(self.old_Filepath)
                self.old_Filepath = ustr(self.old_Filepath)
                # print("old: ",self.old_Filepath)
                basename_old = os.path.basename(
                    os.path.splitext(self.old_Filepath)[0]) + XML_EXT
                xmlPath_old = os.path.join(self.defaultSaveDir_folder, basename_old)

                if bsuccess is False:
                        self.anno_label.setText('XML: ')
                        self.diffcButton.setChecked(False)

                        bsuccess = self.loadPascalXMLByFilename(xmlPath_old, False)
                        self.diffcButton.setChecked(False)
                        if bsuccess is True:
                            self.actions.save.setEnabled(True)
                else:
                    self.anno_label.setText('XML: ' + xmlPath)
                    # self.anno_label.setStyleSheet('color: red')

            self.setWindowTitle(__appname__ + ' ' + filePath)

            # Default : select last item if there is at least one item
            if self.labelList.count():
                self.labelList.setCurrentItem(self.labelList.item(self.labelList.count()-1))
                self.labelList.item(self.labelList.count()-1).setSelected(True)

            self.canvas.setFocus(True)
            return True
        return False


    def loadLabels(self, shapes):
        s = []
        for label, points, line_color, fill_color, difficult in shapes:
            shape = Shape(label=label)
            for x, y in points:
                shape.addPoint(QPointF(x, y))
            shape.difficult = difficult
            shape.close()
            s.append(shape)

            if line_color:
                shape.line_color = QColor(*line_color)
            else:
                shape.line_color = generateColorByText(label)

            if fill_color:
                shape.fill_color = QColor(*fill_color)
            else:
                shape.fill_color = generateColorByText(label)

            self.addLabel(shape)

        self.canvas.loadShapes(s)


    def loadPascalXMLByFilename(self, xmlPath, current=True):
        if self.filePath is None:
            return False
        if os.path.isfile(xmlPath) is False:
            return False

        tVocParseReader = PascalVocReader(xmlPath)
        shapes = tVocParseReader.getShapes()
        self.loadLabels(shapes)
        if current:
            self.canvas.verified = tVocParseReader.verified
        else:
            self.canvas.verified = False

        return True


    def loadPredefinedClasses(self, predefClassesFile):
        if os.path.exists(predefClassesFile) is True:
            with codecs.open(predefClassesFile, 'r', 'utf8') as f:
                for line in f:
                    line = line.strip()
                    if self.labelHist is None:
                        self.labelHist = [line]
                    else:
                        self.labelHist.append(line)


    ## User Dialogs ##
    def loadRecent(self, filename):
        if self.mayContinue():
            self.loadFile(filename)


    def mayContinue(self):
        return not (self.dirty and not self.discardChangesDialog())


    def moveShape(self):
        self.canvas.endMove(copy=False)
        self.setDirty()


    # Callback functions:
    def newShape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        if not self.useDefaultLabelCheckbox.isChecked() or not self.defaultLabelTextLine.text():
            if len(self.labelHist) > 0:
                self.labelDialog = LabelDialog(
                    parent=self, listItem=self.labelHist)

            # Sync single class mode from PR#106
            if self.singleClassMode.isChecked() and self.lastLabel:
                text = self.lastLabel
            else:
                text = self.labelDialog.popUp(text=self.prevLabelText)
                self.lastLabel = text
        else:
            text = self.defaultLabelTextLine.text()

        # Add Chris
        self.diffcButton.setChecked(False)
        if text is not None:
            self.prevLabelText = text
            generate_color = generateColorByText(text)
            shape = self.canvas.setLastLabel(text, generate_color, generate_color)
            self.addLabel(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.setEditing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.setDirty()

            if text not in self.labelHist:
                self.labelHist.append(text)
        else:
            # self.canvas.undoLastLine()
            self.canvas.resetAllLines()


    def nextSess(self):
        if int(self.curSessLineEdit.text()) >= self.sess_no:
            return QMessageBox.warning(self, 'Error', '<p><b>IndexError:</b></p>list index out of range')
        if self.savebtn_label.text() == 'Not saved':
            return QMessageBox.warning(self, 'Warning', 'You forgot to press "Save finished folders" button.')
        self.curSessLineEdit.setText(str(int(self.curSessLineEdit.text()) + 1))
        self.importDirs()


    def noShapes(self):
        return not self.itemsToShapes


    def openAnnotationDialog(self, _value=False):
        if self.filePath is None:
            self.statusBar().showMessage('Please select image first')
            self.statusBar().show()
            return

        path = os.path.dirname(ustr(self.filePath))\
            if self.filePath else '.'
        if self.usingPascalVocFormat:
            filters = "Open Annotation XML file (%s)" % ' '.join(['*.xml'])
            filename = ustr(QFileDialog.getOpenFileName(self,'%s - Choose a xml file' % __appname__, path, filters))
            if filename:
                if isinstance(filename, (tuple, list)):
                    filename = filename[0]
            self.loadPascalXMLByFilename(filename)


    def openDirDialog(self, _value=False, dirpath=None):
        if not self.mayContinue():
            return

        defaultOpenDirPath = dirpath if dirpath else '..'

        if self.imageDirPath and os.path.exists(self.imageDirPath):
            defaultOpenDirPath = self.imageDirPath + '/..'
        elif self.lastOpenDir and os.path.exists(self.lastOpenDir):
            defaultOpenDirPath = self.lastOpenDir + '/../..'
        elif self.filePath and os.path.exists(os.path.dirname(self.filePath)):
            defaultOpenDirPath = os.path.dirname(self.filePath) + '/../..'
        else:
            defaultOpenDirPath = '..'

        self.imageDirPath = ustr(QFileDialog.getExistingDirectory(self, '%s - Open Directory' % __appname__, defaultOpenDirPath,
                                                 QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks))
        if self.imageDirPath == '':
            self.folderListWidget.clear()
        else:
            self.lastOpenDir = self.imageDirPath
            self.job_list_dict = self.importJobs()
            self.curSessLineEdit.setText(str(self.curSession))

            self.job_list = self.job_list_dict[self.logged_id]
            self.sess_no = len(self.job_list)

            self.importDirs()


    def openFile(self, _value=False):
        if not self.mayContinue():
            return
        if self.filePath:
            path = os.path.dirname(ustr(self.filePath))
        elif self.imageDirPath:
            path = self.imageDirPath + '/..'
        else:
            path = '..'
        formats = ['*.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(formats + ['*%s' % LabelFile.suffix])
        filename = QFileDialog.getOpenFileName(self, '%s - Choose Image or Label file' % __appname__, path, filters)
        if isinstance(filename, (tuple, list)):
            filename = filename[0]
        if filename:
            self.loadFile(filename)
            self.filePath = filename


    def openNextImg(self, _value=False):
        # Proceding prev image without dialog if having any label
        if self.autoSaving.isChecked():
            if self.defaultSaveDir is not None:
                if self.dirty is True:
                    self.saveFile()
            else:
                self.changeSavedirDialog()
                return

        if not self.mayContinue():
            return

        if len(self.mImgList) <= 0:
            return

        ###
        self.old_Filepath=self.filePath
        filename = None
        if self.filePath is None:
            filename = self.mImgList[0]
        else:
            currIndex = self.mImgList.index(self.filePath)
            if currIndex + 1 < len(self.mImgList):
                filename = self.mImgList[currIndex + 1]

        if filename:
            self.loadFile(filename)


    def openPrevImg(self, _value=False):
        # Proceding prev image without dialog if having any label
        if self.autoSaving.isChecked():
            if self.defaultSaveDir is not None:
                if self.dirty is True:
                    self.saveFile()
            else:
                self.changeSavedirDialog()
                return

        if not self.mayContinue():
            return

        if len(self.mImgList) <= 0:
            return

        if self.filePath is None:
            return

        # currIndex = self.mImgList.index(self.filePath)
        # if currIndex - 1 >= 0:
        #     filename = self.mImgList[currIndex - 1]
        #     if filename:
        #         self.loadFile(filename)

        ###
        self.old_Filepath = self.filePath
        filename = None
        if self.filePath is None:
            filename = self.mImgList[0]
        else:
            currIndex = self.mImgList.index(self.filePath)
            if currIndex - 1 >= 0:
                filename = self.mImgList[currIndex - 1]

        if filename:
            self.loadFile(filename)


    def paintCanvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoomWidget.value()
        self.canvas.adjustSize()
        self.canvas.update()


    def popLabelListMenu(self, point):
        self.menus.labelList.exec_(self.labelList.mapToGlobal(point))


    def populateModeActions(self):
        if self.beginner():
            tool, menu = self.actions.beginner, self.actions.beginnerContext
        else:
            tool, menu = self.actions.advanced, self.actions.advancedContext
        self.tools.clear()
        addActions(self.tools, tool)
        self.canvas.menus[0].clear()
        addActions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (self.actions.create,) if self.beginner()\
            else (self.actions.createMode, self.actions.editMode)
        addActions(self.menus.edit, actions + self.actions.editMenu)


    def prevSess(self):
        if int(self.curSessLineEdit.text()) - 1 <= 0:
            return QMessageBox.warning(self, 'Error', '<p><b>IndexError:</b></p>list index out of range')
        if self.savebtn_label.text() == 'Not saved':
            return QMessageBox.warning(self, 'Warning', 'You forgot to press "Save finished folders" button.')
        self.curSessLineEdit.setText(str(int(self.curSessLineEdit.text()) - 1))
        self.importDirs()


    def queueEvent(self, function):
        QTimer.singleShot(0, function)


    def remLabel(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapesToItems[shape]
        self.labelList.takeItem(self.labelList.row(item))
        del self.shapesToItems[shape]
        del self.itemsToShapes[item]
        del self.canvas.shapesToItems[shape]
        del self.canvas.itemsToShapes[item]


    def resetAll(self):
        self.settings.reset()
        self.close()
        proc = QProcess()
        proc.startDetached(os.path.abspath(__file__))


    def resetState(self):
        self.itemsToShapes.clear()
        self.shapesToItems.clear()
        self.labelList.clear()
        self.filePath = None
        #self.old_Filepath = None
        self.imageData = None
        self.labelFile = None
        self.canvas.resetState()
        self.labelCoordinates.clear()
        self.canvas.itemsToShapes.clear()
        self.canvas.shapesToItems.clear()


    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull()\
           and self.zoomMode != self.MANUAL_ZOOM:
            self.adjustScale()
        super(MainWindow, self).resizeEvent(event)

    ####
    def saveButtonClicked(self):
        self.savebtn_label.setText('')
        file = QFile(env_path + '/'+ self.logged_id + '_' + str(self.curSession).zfill(2) + '.txt')
        if file.open(QFile.WriteOnly | QFile.Text):
            for check in self.checkList:
                file.write(bytearray(str(check) + '\n', 'utf8'))
        file.close()
        file = QFile(env_path + '/' + 'Statistics.txt')
        if file.open(QFile.Append | QFile.Text):
            file.write(bytearray(self.logged_id + '_' + str(self.curSession).zfill(2) + ', ' + str(datetime.datetime.now()) + ' - {0}/{1}'.format(len(self.checkList), self.n_folder) + '\n', 'utf8'))
        file.close()


    def saveFile(self, _value=False):
        if self.defaultSaveDir_folder is not None and len(ustr(self.defaultSaveDir_folder)):
            if self.filePath:
                imgFileName = os.path.basename(self.filePath)
                savedFileName = os.path.splitext(imgFileName)[0] + XML_EXT
                savedPath = os.path.join(ustr(self.defaultSaveDir_folder), savedFileName)
                ###
                if not os.path.exists(self.defaultSaveDir_folder):
                    os.makedirs(self.defaultSaveDir_folder)
                self._saveFile(savedPath)
        else:
            imgFileDir = os.path.dirname(self.filePath)
            imgFileName = os.path.basename(self.filePath)
            savedFileName = os.path.splitext(imgFileName)[0] + XML_EXT
            savedPath = os.path.join(imgFileDir, savedFileName)
            self._saveFile(savedPath if self.labelFile
                           else self.saveFileDialog())


    def _saveFile(self, annotationFilePath):
        if annotationFilePath and self.saveLabels(annotationFilePath):
            self.setClean()
            self.statusBar().showMessage('Saved to  %s' % annotationFilePath)
            self.statusBar().show()


    def saveFileAs(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._saveFile(self.saveFileDialog())


    def saveFileDialog(self):
        caption = '%s - Choose File' % __appname__
        filters = 'File (*%s)' % LabelFile.suffix
        openDialogPath = self.currentPath()
        dlg = QFileDialog(self, caption, openDialogPath, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filenameWithoutExtension = os.path.splitext(self.filePath)[0]
        dlg.selectFile(filenameWithoutExtension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            return dlg.selectedFiles()[0]
        return ''


    def saveLabels(self, annotationFilePath):
        annotationFilePath = ustr(annotationFilePath)
        if self.labelFile is None:
            self.labelFile = LabelFile()
            self.labelFile.verified = self.canvas.verified

        def format_shape(s):
            return dict(label=s.label,
                        line_color=s.line_color.getRgb(),
                        fill_color=s.fill_color.getRgb(),
                        points=[(p.x(), p.y()) for p in s.points],
                       # add chris
                        difficult = s.difficult)

        shapes = [format_shape(shape) for shape in self.canvas.shapes]
        # Can add differrent annotation formats here
        try:
            if self.usingPascalVocFormat is True:
                # print ('Img: ' + self.filePath + ' -> Its xml: ' + annotationFilePath)
                self.labelFile.savePascalVocFormat(annotationFilePath, shapes, self.filePath, self.imageData,
                                                   self.lineColor.getRgb(), self.fillColor.getRgb())
            else:
                self.labelFile.save(annotationFilePath, shapes, self.filePath, self.imageData,
                                    self.lineColor.getRgb(), self.fillColor.getRgb())
            return True
        except LabelFileError as e:
            self.errorMessage(u'Error saving label data', u'<b>%s</b>' % e)
            return False


    def scaleFitWidth(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()


    def scaleFitWindow(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2


    def scanAllImages(self, folderPath):
        extensions = ['.jpeg', '.jpg', '.png', '.bmp']
        images = []

        for root, dirs, files in os.walk(folderPath):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relativePath = os.path.join(root, file)
                    path = ustr(os.path.abspath(relativePath))
                    images.append(path)
        images.sort(key=lambda x: x.lower())
        return images

    ###
    def scanAllDirs(self, folderPath):

        pre_dirs = os.listdir(folderPath)

        # for root, dirs, files in os.walk(folderPath):
        #     for file in files:
        #         if file.lower().endswith(tuple(extensions)):
        #             relativePath = os.path.join(root, file)
        #             path = ustr(os.path.abspath(relativePath))
        #             images.append(path)

        # pre_dirs.sort(key=lambda x: x.lower())
        pre_dirs_int = [int(dir) for dir in pre_dirs]
        pre_dirs_int.sort()
        pre_dirs = [ str(x) for x in pre_dirs_int]
        return pre_dirs


    def scrollRequest(self, delta, orientation):
        units = - delta / (8 * 15)
        bar = self.scrollBars[orientation]
        bar.setValue(bar.value() + bar.singleStep() * units)


    def setAdvanced(self):
        self.tools.clear()
        addActions(self.tools, self.actions.advanced)


    def setBeginner(self):
        self.tools.clear()
        addActions(self.tools, self.actions.beginner)


    def setClean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)


    def setCreateMode(self):
        assert self.advanced()
        self.toggleDrawMode(False)


    def setDirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)


    def setEditMode(self):
        assert self.advanced()
        self.toggleDrawMode(True)
        self.labelSelectionChanged()


    def set_end(self):
        if self.end_img_file == '':
            self.end_img_file = self.filePath
        elif self.end_img_file == self.filePath:
            self.end_img_file = ''
        else:
            self.end_img_file = self.filePath

        if self.end_img_file != '':
            self.end_label.setText('End: ' + os.path.basename(self.end_img_file))

            foldername = os.path.dirname(self.filePath).split('/')[-1]
            filedir = os.path.join(results_path, foldername)
            if not os.path.exists(filedir):
                os.makedirs(filedir)

            file = QFile(os.path.join(filedir, 'start_end.txt'))
            if file.open(QFile.WriteOnly | QFile.Text):
                file.write(bytearray(self.start_img_file + '\n', 'utf8'))
                file.write(bytearray(self.end_img_file + '\n', 'utf8'))
            file.close()


    def setFitWidth(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjustScale()


    def setFitWindow(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoomMode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjustScale()


    def set_start(self):
        if self.start_img_file == '':
            self.start_img_file = self.filePath
        elif self.start_img_file == self.filePath:
            self.start_img_file = ''
        else:
            self.start_img_file = self.filePath

        if self.start_img_file != '':
            self.start_label.setText('Start: ' + os.path.basename(self.start_img_file))

            foldername = os.path.dirname(self.filePath).split('/')[-1]
            filedir = os.path.join(results_path, foldername)
            if not os.path.exists(filedir):
                os.makedirs(filedir)

            file = QFile(os.path.join(filedir, 'start_end.txt'))
            if file.open(QFile.WriteOnly | QFile.Text):
                file.write(bytearray(self.start_img_file + '\n', 'utf8'))
                file.write(bytearray(self.end_img_file + '\n', 'utf8'))
            file.close()


    def setZoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.MANUAL_ZOOM
        self.zoomWidget.setValue(value)


    # React to canvas signals.
    def shapeSelectionChanged(self, selected=False):
        if self._noSelectionSlot:
            self._noSelectionSlot = False
        else:
            shape = self.canvas.selectedShape
            if shape:
                self.shapesToItems[shape].setSelected(True)
            else:
                self.labelList.clearSelection()
        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)


    def showInfoDialog(self):
        msg = u'Name:{0} \nApp Version:{1} \n{2} '.format(__appname__, __version__, sys.version_info)
        QMessageBox.information(self, u'Information', msg)


    ## Callbacks ##
    def showTutorialDialog(self):
        subprocess.Popen([self.screencastViewer, self.screencast])


    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)


    def toggleActions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)


    def toggleAdvancedMode(self, value=True):
        self._beginner = not value
        self.canvas.setEditing(True)
        self.populateModeActions()
        self.editButton.setVisible(not value)
        if value:
            self.actions.createMode.setEnabled(True)
            self.actions.editMode.setEnabled(False)
            self.dock.setFeatures(self.dock.features() | self.dockFeatures)
        else:
            self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)


    def toggleDrawingSensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        if not drawing and self.beginner():
            # Cancel creation.
            print('Cancel creation.')
            self.canvas.setEditing(True)
            self.canvas.restoreCursor()
            self.actions.create.setEnabled(True)


    def toggleDrawMode(self, edit=True):
        self.canvas.setEditing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)


    def togglePolygons(self, value):
        for item, shape in self.itemsToShapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)


    def updateFileMenu(self):
        currFilePath = self.filePath

        def exists(filename):
            return os.path.exists(filename)
        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recentFiles if f !=
                 currFilePath and exists(f)]
        for i, f in enumerate(files):
            icon = newIcon('labels')
            action = QAction(
                icon, '&%d %s' % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.loadRecent, f))
            menu.addAction(action)


    def zoomRequest(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scrollBars[Qt.Horizontal]
        v_bar = self.scrollBars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scrollArea.width()
        h = self.scrollArea.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta / (8 * 15)
        scale = 10
        self.addZoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = h_bar.value() + move_x * d_h_bar_max
        new_v_bar_value = v_bar.value() + move_y * d_v_bar_max

        h_bar.setValue(new_h_bar_value)
        v_bar.setValue(new_v_bar_value)


def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        with open(filename, 'rb') as f:
            return f.read()
    except:
        return default