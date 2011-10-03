# -*- coding: utf-8 -*-
"""
glifLib.py -- Generic module for reading and writing the .glif format.

More info about the .glif format (GLyphInterchangeFormat) can be found here:

	http://unifiedfontobject.org

The main class in this module is GlyphSet. It manages a set of .glif files
in a folder. It offers two ways to read glyph data, and one way to write
glyph data. See the class doc string for details.
"""

import os
from cStringIO import StringIO
from xmlTreeBuilder import buildTree, stripCharacterData
from robofab.pens.pointPen import AbstractPointPen
from plistlib import readPlist, writePlistToString
from filenames import userNameToFileName
from validators import genericTypeValidator, colorValidator, guidelinesValidator, identifierValidator, imageValidator

try:
	set
except NameError:
	from sets import Set as set

__all__ = [
	"GlyphSet",
	"GlifLibError",
	"readGlyphFromString", "writeGlyphToString",
	"glyphNameToFileName"
]


class GlifLibError(Exception): pass


# -------------------------
# Reading and Writing Modes
# -------------------------

if os.name == "mac":
	WRITE_MODE = "wb"  # use unix line endings, even with Classic MacPython
	READ_MODE = "rb"
else:
	WRITE_MODE = "w"
	READ_MODE = "r"

# ---------
# Constants
# ---------

LAYERINFO_FILENAME = "layerinfo.plist"
supportedUFOFormatVersions = [1, 2, 3]
supportedGLIFFormatVersions = [1, 2]


# ------------
# Simple Glyph
# ------------

class Glyph(object):

	"""
	Minimal glyph object. It has no glyph attributes until either
	the draw() or the drawPoint() method has been called.
	"""

	def __init__(self, glyphName, glyphSet):
		self.glyphName = glyphName
		self.glyphSet = glyphSet

	def draw(self, pen):
		"""
		Draw this glyph onto a *FontTools* Pen.
		"""
		from robofab.pens.adapterPens import PointToSegmentPen
		pointPen = PointToSegmentPen(pen)
		self.drawPoints(pointPen)

	def drawPoints(self, pointPen):
		"""
		Draw this glyph onto a PointPen.
		"""
		self.glyphSet.readGlyph(self.glyphName, self, pointPen)


# ---------
# Glyph Set
# ---------

class GlyphSet(object):

	"""
	GlyphSet manages a set of .glif files inside one directory.

	GlyphSet's constructor takes a path to an existing directory as it's
	first argument. Reading glyph data can either be done through the
	readGlyph() method, or by using GlyphSet's dictionary interface, where
	the keys are glyph names and the values are (very) simple glyph objects.

	To write a glyph to the glyph set, you use the writeGlyph() method.
	The simple glyph objects returned through the dict interface do not
	support writing, they are just a convenient way to get at the glyph data.
	"""

	glyphClass = Glyph

	def __init__(self, dirName, glyphNameToFileNameFunc=None, ufoFormatVersion=3):
		"""
		'dirName' should be a path to an existing directory.

		The optional 'glyphNameToFileNameFunc' argument must be a callback
		function that takes two arguments: a glyph name and the GlyphSet
		instance. It should return a file name (including the .glif
		extension). The glyphNameToFileName function is called whenever
		a file name is created for a given glyph name.
		"""
		self.dirName = dirName
		if ufoFormatVersion not in supportedUFOFormatVersions:
			raise GlifLibError("Unsupported UFO format version: %s" % ufoFormatVersion)
		self.ufoFormatVersion = ufoFormatVersion
		if glyphNameToFileNameFunc is None:
			glyphNameToFileNameFunc = glyphNameToFileName
		self.glyphNameToFileName = glyphNameToFileNameFunc
		self.rebuildContents()
		self._reverseContents = None
		self._glifCache = {}

	def rebuildContents(self):
		"""
		Rebuild the contents dict by loading contents.plist.
		"""
		contentsPath = os.path.join(self.dirName, "contents.plist")
		if not os.path.exists(contentsPath):
			# missing, consider the glyphset empty.
			contents = {}
		else:
			contents = self._readPlist(contentsPath)
		# validate the contents
		invalidFormat = False
		if not isinstance(contents, dict):
			invalidFormat = True
		else:
			for name, fileName in contents.items():
				if not isinstance(name, basestring):
					invalidFormat = True
				if not isinstance(fileName, basestring):
					invalidFormat = True
				elif not os.path.exists(os.path.join(self.dirName, fileName)):
					raise GlifLibError("contents.plist references a file that does not exist: %s" % fileName)
		if invalidFormat:
			raise GlifLibError("contents.plist is not properly formatted")
		self.contents = contents
		self._reverseContents = None

	def getReverseContents(self):
		"""
		Return a reversed dict of self.contents, mapping file names to
		glyph names. This is primarily an aid for custom glyph name to file
		name schemes that want to make sure they don't generate duplicate
		file names. The file names are converted to lowercase so we can
		reliably check for duplicates that only differ in case, which is
		important for case-insensitive file systems.
		"""
		if self._reverseContents is None:
			d = {}
			for k, v in self.contents.iteritems():
				d[v.lower()] = k
			self._reverseContents = d
		return self._reverseContents

	def writeContents(self):
		"""
		Write the contents.plist file out to disk. Call this method when
		you're done writing glyphs.
		"""
		contentsPath = os.path.join(self.dirName, "contents.plist")
		# We need to force Unix line endings, even in OS9 MacPython in FL,
		# so we do the writing to file ourselves.
		plist = writePlistToString(self.contents)
		f = open(contentsPath, WRITE_MODE)
		f.write(plist)
		f.close()

	# layer info

	def readLayerInfo(self, info):
		path = os.path.join(self.dirName, LAYERINFO_FILENAME)
		if not os.path.exists(path):
			return
		infoDict = self._readPlist(path)
		if not isinstance(infoDict, dict):
			raise GlifLibError("layerinfo.plist is not properly formatted.")
		infoDict = validateLayerInfoVersion3Data(infoDict)
		# populate the object
		for attr, value in infoDict.items():
			try:
				setattr(info, attr, value)
			except AttributeError:
				raise GlifLibError("The supplied layer info object does not support setting a necessary attribute (%s)." % attr)

	def writeLayerInfo(self, info):
		if self.ufoFormatVersion < 3:
			raise GlifLibError("layerinfo.plist is not allowed in UFO %d." % self.ufoFormatVersion)
		# gather data
		infoData = {}
		for attr in layerInfoVersion3ValueData.keys():
			if hasattr(info, attr):
				try:
					value = getattr(info, attr)
				except AttributeError:
					raise GlifLibError("The supplied info object does not support getting a necessary attribute (%s)." % attr)
				if value is None:
					continue
				infoData[attr] = value
		# validate
		infoData = validateLayerInfoVersion3Data(infoData)
		# write file
		path = os.path.join(self.dirName, LAYERINFO_FILENAME)
		plist = writePlistToString(infoData)
		f = open(path, WRITE_MODE)
		f.write(plist)
		f.close()

	# read caching

	def getGLIF(self, glyphName):
		"""
		Get the raw GLIF text for a given glyph name. This only works
		for GLIF files that are already on disk.

		This method is useful in situations when the raw XML needs to be
		read from a glyph set for a particular glyph before fully parsing
		it into an object structure via the readGlyph method.

		Internally, this method will load a GLIF the first time it is
		called and then cache it. The next time this method is called
		the GLIF will be pulled from the cache if the file's modification
		time has not changed since the GLIF was cached. For memory
		efficiency, the cached GLIF will be purged by various other methods
		such as readGlyph.
		"""
		needRead = False
		fileName = self.contents.get(glyphName)
		path = None
		if fileName is not None:
			path = os.path.join(self.dirName, fileName)
		if glyphName not in self._glifCache:
			needRead = True
		elif fileName is not None and os.path.getmtime(path) != self._glifCache[glyphName][1]:
			needRead = True
		if needRead:
			fileName = self.contents[glyphName]
			if not os.path.exists(path):
				raise KeyError, glyphName
			f = open(path, "rb")
			text = f.read()
			f.close()
			self._glifCache[glyphName] = (text, os.path.getmtime(path))
		return self._glifCache[glyphName][0]

	def _purgeCachedGLIF(self, glyphName):
		if glyphName in self._glifCache:
			del self._glifCache[glyphName]

	# reading/writing API

	def readGlyph(self, glyphName, glyphObject=None, pointPen=None):
		"""
		Read a .glif file for 'glyphName' from the glyph set. The
		'glyphObject' argument can be any kind of object (even None);
		the readGlyph() method will attempt to set the following
		attributes on it:
			"width"     the advance with of the glyph
			"unicodes"  a list of unicode values for this glyph
			"note"      a string
			"lib"       a dictionary containing custom data

		All attributes are optional, in two ways:
			1) An attribute *won't* be set if the .glif file doesn't
			   contain data for it. 'glyphObject' will have to deal
			   with default values itself.
			2) If setting the attribute fails with an AttributeError
			   (for example if the 'glyphObject' attribute is read-
			   only), readGlyph() will not propagate that exception,
			   but ignore that attribute.

		To retrieve outline information, you need to pass an object
		conforming to the PointPen protocol as the 'pointPen' argument.
		This argument may be None if you don't need the outline data.

		readGlyph() will raise KeyError if the glyph is not present in
		the glyph set.
		"""
		text = self.getGLIF(glyphName)
		self._purgeCachedGLIF(glyphName)
		tree = _glifTreeFromFile(StringIO(text))
		_readGlyphFromTree(tree, glyphObject, pointPen)

	def writeGlyph(self, glyphName, glyphObject=None, drawPointsFunc=None, formatVersion=None):
		"""
		Write a .glif file for 'glyphName' to the glyph set. The
		'glyphObject' argument can be any kind of object (even None);
		the writeGlyph() method will attempt to get the following
		attributes from it:
			"width"     the advance with of the glyph
			"unicodes"  a list of unicode values for this glyph
			"note"      a string
			"lib"       a dictionary containing custom data

		All attributes are optional: if 'glyphObject' doesn't
		have the attribute, it will simply be skipped.

		To write outline data to the .glif file, writeGlyph() needs
		a function (any callable object actually) that will take one
		argument: an object that conforms to the PointPen protocol.
		The function will be called by writeGlyph(); it has to call the
		proper PointPen methods to transfer the outline to the .glif file.

		The GLIF format version will be chosen based on the ufoFormatVersion
		passed during the creation of this object. If a particular format
		version is desired, it can be passed with the formatVersion argument.
		"""
		if formatVersion is None:
			if self.ufoFormatVersion >= 3:
				formatVersion = 2
			else:
				formatVersion = 1
		else:
			if formatVersion not in supportedGLIFFormatVersions:
				raise GlifLibError("Unsupported GLIF format version: %s" % formatVersion)
			if formatVersion == 2 and self.ufoFormatVersion < 3:
				raise GlifLibError("Unsupported GLIF format version (%d) for UFO format version %d." % (formatVersion, self.ufoFormatVersion))
		self._purgeCachedGLIF(glyphName)
		data = writeGlyphToString(glyphName, glyphObject, drawPointsFunc, formatVersion=formatVersion)
		fileName = self.contents.get(glyphName)
		if fileName is None:
			fileName = self.glyphNameToFileName(glyphName, self)
			self.contents[glyphName] = fileName
			if self._reverseContents is not None:
				self._reverseContents[fileName.lower()] = glyphName
		path = os.path.join(self.dirName, fileName)
		if os.path.exists(path):
			f = open(path, READ_MODE)
			oldData = f.read()
			f.close()
			if data == oldData:
				return
		f = open(path, WRITE_MODE)
		f.write(data)
		f.close()

	def deleteGlyph(self, glyphName):
		"""Permanently delete the glyph from the glyph set on disk. Will
		raise KeyError if the glyph is not present in the glyph set.
		"""
		self._purgeCachedGLIF(glyphName)
		fileName = self.contents[glyphName]
		os.remove(os.path.join(self.dirName, fileName))
		if self._reverseContents is not None:
			del self._reverseContents[self.contents[glyphName].lower()]
		del self.contents[glyphName]

	# dict-like support

	def keys(self):
		return self.contents.keys()

	def has_key(self, glyphName):
		return glyphName in self.contents

	__contains__ = has_key

	def __len__(self):
		return len(self.contents)

	def __getitem__(self, glyphName):
		if glyphName not in self.contents:
			raise KeyError, glyphName
		return self.glyphClass(glyphName, self)

	# quickly fetch unicode values

	def getUnicodes(self):
		"""
		Return a dictionary that maps all glyph names to lists containing
		the unicode value[s] for that glyph, if any. This parses the .glif
		files partially, so is a lot faster than parsing all files completely.
		"""
		unicodes = {}
		for glyphName in self.contents.keys():
			text = self.getGLIF(glyphName)
			unicodes[glyphName] = _fetchUnicodes(text)
		return unicodes

	# internal methods

	def _readPlist(self, path):
		try:
			data = readPlist(path)
			return data
		except:
			raise GlifLibError("The file %s could not be read." % path)


# -----------------------
# Glyph Name to File Name
# -----------------------

def glyphNameToFileName(glyphName, glyphSet):
	"""
	Wrapper around the userNameToFileName function in filenames.py
	"""
	existing = [name.lower() for name in glyphSet.contents.values()]
	if not isinstance(glyphName, unicode):
		try:
			new = unicode(glyphName)
			glyphName = new
		except UnicodeDecodeError:
			pass
	return userNameToFileName(glyphName, existing=existing, suffix=".glif")

# -----------------------
# GLIF To and From String
# -----------------------

def readGlyphFromString(aString, glyphObject=None, pointPen=None):
	"""
	Read .glif data from a string into a glyph object.

	The 'glyphObject' argument can be any kind of object (even None);
	the readGlyphFromString() method will attempt to set the following
	attributes on it:
		"width"     the advance with of the glyph
		"unicodes"  a list of unicode values for this glyph
		"note"      a string
		"lib"       a dictionary containing custom data

	All attributes are optional, in two ways:
		1) An attribute *won't* be set if the .glif file doesn't
		   contain data for it. 'glyphObject' will have to deal
		   with default values itself.
		2) If setting the attribute fails with an AttributeError
		   (for example if the 'glyphObject' attribute is read-
		   only), readGlyphFromString() will not propagate that
		   exception, but ignore that attribute.

	To retrieve outline information, you need to pass an object
	conforming to the PointPen protocol as the 'pointPen' argument.
	This argument may be None if you don't need the outline data.
	"""
	tree = _glifTreeFromFile(StringIO(aString))
	_readGlyphFromTree(tree, glyphObject, pointPen)


def writeGlyphToString(glyphName, glyphObject=None, drawPointsFunc=None, writer=None, formatVersion=2):
	"""
	Return .glif data for a glyph as a UTF-8 encoded string.
	The 'glyphObject' argument can be any kind of object (even None);
	the writeGlyphToString() method will attempt to get the following
	attributes from it:
		"width"     the advance width of the glyph
		"height"    the advance height of the glyph
		"unicodes"  a list of unicode values for this glyph
		"note"      a string
		"lib"       a dictionary containing custom data

	All attributes are optional: if 'glyphObject' doesn't
	have the attribute, it will simply be skipped.

	To write outline data to the .glif file, writeGlyphToString() needs
	a function (any callable object actually) that will take one
	argument: an object that conforms to the PointPen protocol.
	The function will be called by writeGlyphToString(); it has to call the
	proper PointPen methods to transfer the outline to the .glif file.

	The GLIF format version can be specified with the formatVersion argument.
	"""
	if writer is None:
		from xmlWriter import XMLWriter
		aFile = StringIO()
		writer = XMLWriter(aFile, encoding="UTF-8")
	else:
		aFile = None
	identifiers = set()
	# start
	if glyphName == "":
		raise GlifLibError("The glyph name is empty.")
	if not isinstance(glyphName, basestring):
		raise GlifLibError("The glyph name is not properly formatted.")
	writer.begintag("glyph", [("name", glyphName), ("format", formatVersion)])
	writer.newline()
	# advance
	_writeAdvance(glyphObject, writer)
	# unicodes
	if getattr(glyphObject, "unicodes", None):
		_writeUnicodes(glyphObject, writer)
	# note
	if getattr(glyphObject, "note", None):
		_writeNote(glyphObject, writer)
	# image
	if formatVersion >= 2 and getattr(glyphObject, "image", None):
		_writeImage(glyphObject, writer)
	# guidelines
	if formatVersion >= 2 and getattr(glyphObject, "guidelines", None):
		_writeGuidelines(glyphObject, writer, identifiers)
	# outline
	if drawPointsFunc is not None:
		writer.begintag("outline")
		writer.newline()
		pen = GLIFPointPen(writer, identifiers=identifiers)
		drawPointsFunc(pen)
		writer.endtag("outline")
		writer.newline()
	# lib
	if getattr(glyphObject, "lib", None):
		_writeLib(glyphObject, writer)
	# end
	writer.endtag("glyph")
	writer.newline()
	# return the appropriate value
	if aFile is not None:
		return aFile.getvalue()
	else:
		return None

def _writeAdvance(glyphObject, writer):
	width = getattr(glyphObject, "width", None)
	if width is not None:
		if not isinstance(width, (int, float)):
			raise GlifLibError("width attribute must be int or float")
	height = getattr(glyphObject, "height", None)
	if height is not None:
		if not isinstance(height, (int, float)):
			raise GlifLibError("height attribute must be int or float")
	if width is not None and height is not None:
		writer.simpletag("advance", width=str(width), height=str(height))
		writer.newline()
	elif width is not None:
		writer.simpletag("advance", width=str(width))
		writer.newline()
	elif height is not None:
		writer.simpletag("advance", height=str(height))
		writer.newline()

def _writeUnicodes(glyphObject, writer):
	unicodes = getattr(glyphObject, "unicodes", None)
	if isinstance(unicodes, int):
		unicodes = [unicodes]
	seen = set()
	for code in unicodes:
		if not isinstance(code, int):
			raise GlifLibError("unicode values must be int")
		if code in seen:
			continue
		seen.add(code)
		hexCode = hex(code)[2:].upper()
		if len(hexCode) < 4:
			hexCode = "0" * (4 - len(hexCode)) + hexCode
		writer.simpletag("unicode", hex=hexCode)
		writer.newline()

def _writeNote(glyphObject, writer):
	note = getattr(glyphObject, "note", None)
	if not isinstance(note, (str, unicode)):
		raise GlifLibError("note attribute must be str or unicode")
	note = note.encode("utf-8")
	writer.begintag("note")
	writer.newline()
	for line in note.splitlines():
		writer.write(line.strip())
		writer.newline()
	writer.endtag("note")
	writer.newline()

def _writeImage(glyphObject, writer):
	image = getattr(glyphObject, "image", None)
	if not imageValidator(image):
		raise GlifLibError("image attribute must be a dict or dict-like object with the proper structure.")
	attrs = [
		("fileName", image["fileName"])
	]
	for attr, default in _transformationInfo:
		value = image.get(attr, default)
		if value != default:
			attrs.append((attr, str(value)))
	color = image.get("color")
	if color is not None:
		attrs.append(("color", color))
	writer.simpletag("image", attrs)
	writer.newline()

def _writeGuidelines(glyphObject, writer, identifiers):
	guidelines = getattr(glyphObject, "guidelines", [])
	if not guidelinesValidator(guidelines):
		raise GlifLibError("guidelines attribute does not have the proper structure.")
	for guideline in guidelines:
		attrs = []
		x = guideline.get("x")
		if x is not None:
			attrs.append(("x", str(x)))
		y = guideline.get("y")
		if y is not None:
			attrs.append(("y", str(y)))
		angle = guideline.get("angle")
		if angle is not None:
			attrs.append(("angle", str(angle)))
		name = guideline.get("name")
		if name is not None:
			attrs.append(("name", name))
		color = guideline.get("color")
		if color is not None:
			attrs.append(("color", color))
		identifier = guideline.get("identifier")
		if identifier is not None:
			if identifier in identifiers:
				raise GlifLibError("identifier used more than once: %s" % identifier)
			attrs.append(("identifier", identifier))
			identifiers.add(identifier)
		writer.simpletag("guideline", attrs)
		writer.newline()

def _writeLib(glyphObject, writer):
	from ufoLib.plistlib import PlistWriter
	lib = getattr(glyphObject, "lib", None)
	if not isinstance(lib, dict):
		raise GlifLibError("lib attribute must be a dict.")
	writer.begintag("lib")
	writer.newline()
	plistWriter = PlistWriter(writer.file, indentLevel=writer.indentlevel,
			indent=writer.indentwhite, writeHeader=False)
	plistWriter.writeValue(lib)
	writer.endtag("lib")
	writer.newline()

# -----------------------
# layerinfo.plist Support
# -----------------------

layerInfoVersion3ValueData = {
	"color"			: dict(type=basestring, valueValidator=colorValidator),
	"lib"			: dict(type=dict, valueValidator=genericTypeValidator)
}

def validateLayerInfoVersion3ValueForAttribute(attr, value):
	"""
	This performs very basic validation of the value for attribute
	following the UFO 3 fontinfo.plist specification. The results
	of this should not be interpretted as *correct* for the font
	that they are part of. This merely indicates that the value
	is of the proper type and, where the specification defines
	a set range of possible values for an attribute, that the
	value is in the accepted range.
	"""
	if attr not in layerInfoVersion3ValueData:
		return False
	dataValidationDict = layerInfoVersion3ValueData[attr]
	valueType = dataValidationDict.get("type")
	validator = dataValidationDict.get("valueValidator")
	valueOptions = dataValidationDict.get("valueOptions")
	# have specific options for the validator
	if valueOptions is not None:
		isValidValue = validator(value, valueOptions)
	# no specific options
	else:
		if validator == genericTypeValidator:
			isValidValue = validator(value, valueType)
		else:
			isValidValue = validator(value)
	return isValidValue

def validateLayerInfoVersion3Data(infoData):
	"""
	This performs very basic validation of the value for infoData
	following the UFO 3 layerinfo.plist specification. The results
	of this should not be interpretted as *correct* for the font
	that they are part of. This merely indicates that the values
	are of the proper type and, where the specification defines
	a set range of possible values for an attribute, that the
	value is in the accepted range.
	"""
	validInfoData = {}
	for attr, value in infoData.items():
		if attr not in layerInfoVersion3ValueData:
			raise GlifLibError("Unknown attribute %s." % attr)
		isValidValue = validateLayerInfoVersion3ValueForAttribute(attr, value)
		if not isValidValue:
			raise GlifLibError("Invalid value for attribute %s (%s)." % (attr, repr(value)))
		else:
			validInfoData[attr] = value
	return validInfoData

# -----------------
# GLIF Tree Support
# -----------------

def _stripGlyphXMLTree(nodes):
	for node in nodes:
		if len(node) != 3:
			raise GlifLibError("Invalid GLIF structure.")
		element, attrs, children = node
		# "lib" is formatted as a plist, so we need unstripped
		# character data so we can support strings with leading or
		# trailing whitespace. Do strip everything else.
		recursive = (element != "lib")
		stripCharacterData(children, recursive=recursive)

def _glifTreeFromFile(aFile):
	tree = buildTree(aFile, stripData=False)
	stripCharacterData(tree[2], recursive=False)
	if tree[0] != "glyph":
		raise GlifLibError("The GLIF is not properly formatted.")
	_stripGlyphXMLTree(tree[2])
	return tree

def _readGlyphFromTree(tree, glyphObject=None, pointPen=None):
	# quick format validation
	formatError = False
	if len(tree) != 3:
		formatError = True
	else:
		if tree[0] != "glyph":
			formatError = True
	if formatError:
		raise GlifLibError("GLIF data is not properly formatted.")
	# check the format version
	formatVersion = tree[1].get("format", "undefined")
	try:
		v = int(formatVersion)
		formatVersion = v
	except ValueError:
		pass
	if formatVersion not in supportedGLIFFormatVersions:
		raise GlifLibError, "Unsupported GLIF format version: %s" % formatVersion
	# get the name
	glyphName = tree[1].get("name")
	if glyphName == "":
		raise GlifLibError("Empty glyph name in GLIF.")
	if glyphName and glyphObject is not None:
		_relaxedSetattr(glyphObject, "name", glyphName)
	# populate the sub elements
	unicodes = []
	guidelines = []
	haveSeenAdvance = haveSeenImage = haveSeenOutline = haveSeenLib = haveSeenNote = False
	identifiers = set()
	for element, attrs, children in tree[2]:
		if element == "outline":
			if haveSeenOutline:
				raise GlifLibError("The outline element occurs more than once.")
			if attrs:
				raise GlifLibError("The outline element contains unknwon attributes.")
			haveSeenOutline = True
			if pointPen is not None:
				buildOutline(pointPen, children, formatVersion, identifiers)
		elif glyphObject is None:
			continue
		elif element == "advance":
			if haveSeenAdvance:
				raise GlifLibError("The advance element occurs more than once.")
			haveSeenAdvance = True
			width = _number(attrs.get("width", 0))
			_relaxedSetattr(glyphObject, "width", width)
			height = _number(attrs.get("height", 0))
			_relaxedSetattr(glyphObject, "height", height)
		elif element == "unicode":
			try:
				v = attrs.get("hex", "undefined")
				v = int(v, 16)
				if v not in unicodes:
					unicodes.append(v)
			except ValueError:
				raise GlifLibError("Illegal value for hex attribute of unicode element.")
		elif element == "guideline":
			if formatVersion == 1:
				raise GlifLibError("The guideline element is not allowed in GLIF format 1.")
			if len(children):
				raise GlifLibError("Unknown children in guideline element.")
			for attr in ("x", "y", "angle"):
				if attr in attrs:
					attrs[attr] = _number(attrs[attr])
			guidelines.append(attrs)
		elif element == "image":
			if formatVersion == 1:
				raise GlifLibError("The image element is not allowed in GLIF format 1.")
			if haveSeenImage:
				raise GlifLibError("The image element occurs more than once.")
			if len(children):
				raise GlifLibError("Unknown children in image element.")
			haveSeenImage = True
			imageData = attrs
			for attr, d in _transformationInfo:
				if attr in imageData:
					imageData[attr] = _number(imageData[attr])
			if not imageValidator(imageData):
				raise GlifLibError("The image element is not properly formatted.")
			_relaxedSetattr(glyphObject, "image", imageData)
		elif element == "note":
			if haveSeenNote:
				raise GlifLibError("The note element occurs more than once.")
			haveSeenNote = True
			# XXX validate?
			rawNote = "\n".join(children)
			lines = rawNote.split("\n")
			lines = [line.strip() for line in lines]
			note = "\n".join(lines)
			_relaxedSetattr(glyphObject, "note", note)
		elif element == "lib":
			if haveSeenLib:
				raise GlifLibError("The lib element occurs more than once.")
			haveSeenLib = True
			# XXX validate?
			from plistFromTree import readPlistFromTree
			assert len(children) == 1
			lib = readPlistFromTree(children[0])
			_relaxedSetattr(glyphObject, "lib", lib)
		else:
			raise GlifLibError("Unknown element in GLIF: %s" % element)
	# set the collected unicodes
	if unicodes:
		_relaxedSetattr(glyphObject, "unicodes", unicodes)
	# set the collected guidelines
	if formatVersion > 1 and guidelines:
		if not guidelinesValidator(guidelines, identifiers):
			raise GlifLibError("The guidelines are improperly formatted.")
		_relaxedSetattr(glyphObject, "guidelines", guidelines)

# ----------------
# GLIF to PointPen
# ----------------

componentAttributes = set(["base", "xScale", "xyScale", "yxScale", "yScale", "xOffset", "yOffset", "identifier"])
contourAttributes = set(["identifier"])
pointAttributes = set(["x", "y", "type", "smooth", "name", "identifier"])
pointSmoothOptions = set(("no", "yes"))
pointTypeOptions = set(["move", "line", "offcurve", "curve", "qcurve"])

def buildOutline(pen, xmlNodes, formatVersion, identifiers):
	for node in xmlNodes:
		if len(node) != 3:
			raise GlifLibError("The outline element is not properly structured.")
		element, attrs, children = node
		if element == "contour":
			_buildOutlineContour(pen, (attrs, children), formatVersion, identifiers)
		elif element == "component":
			_buildOutlineComponent(pen, (attrs, children), formatVersion, identifiers)
		else:
			raise GlifLibError("Unknown element in outline element: %s" % element)

def _buildOutlineContour(pen, (attrs, children), formatVersion, identifiers):
	# search for unknown attributes
	if set(attrs.keys()) - contourAttributes:
		raise GlifLibError("Unknown attributes in contour element.")
	# identifier is not required but it is not part of format 1
	identifier = attrs.get("identifier")
	if identifier is not None and formatVersion == 1:
		raise GlifLibError("The contour identifier attribute is not allowed in GLIF format 1.")
	if identifier is not None:
		if identifier in identifiers:
			raise GlifLibError("The identifier %s is used more than once." % identifier)
		if not identifierValidator(identifier):
			raise GlifLibError("The contour identifier %s is not valid." % identifier)
		identifiers.add(identifier)
	# try to pass the identifier attribute
	try:
		pen.beginPath(identifier=identifier)
	except TypeError:
		pen.beginPath()
		raise DeprecationWarning("The beginPath method needs an identifier kwarg. The contour's identifier value has been discarded.")
	# points
	if children:
		# make sure that only points are here
		for e, d, c in children:
			if e != "point":
				raise GlifLibError("The contour element contains an unknown element: %s" % e)
		# loop through the points very quickly to make sure that the number of off-curves is correct
		_validateSegmentStructures(children)
		# interpret the points
		_buildOutlinePoints(pen, children, formatVersion, identifiers)
	# done
	pen.endPath()

def _buildOutlineComponent(pen, (attrs, children), formatVersion, identifiers):
	# unknown child element of contour
	if len(children):
		raise GlifLibError("Unknown child elements of component element." % subElement)
	# search for unknown attributes
	if set(attrs.keys()) - componentAttributes:
		raise GlifLibError("Unknown attributes in component element.")
	# base is required
	baseGlyphName = attrs.get("base")
	if baseGlyphName is None:
		raise GlifLibError("The base attribute is not defined in the component.")
	# transformation is not required
	transformation = []
	for attr, default in _transformationInfo:
		value = attrs.get(attr)
		if value is None:
			value = default
		else:
			value = _number(value)
		transformation.append(value)
	# identifier is not required but it is not part of format 1
	identifier = attrs.get("identifier")
	if identifier is not None and formatVersion == 1:
		raise GlifLibError("The component identifier attribute is not allowed in GLIF format 1.")
	if identifier is not None:
		if identifier in identifiers:
			raise GlifLibError("The identifier %s is used more than once." % identifier)
		if not identifierValidator(identifier):
			raise GlifLibError("The identifier %s is not valid." % identifier)
		identifiers.add(identifier)
	# try to pass the identifier attribute
	try:
		pen.addComponent(baseGlyphName, tuple(transformation), identifier=identifier)
	except TypeError:
		pen.addComponent(baseGlyphName, tuple(transformation))
		raise DeprecationWarning("The addComponent method needs an identifier kwarg. The component's identifier value has been discarded.")

def _validateSegmentStructures(children):
	pointTypes = [a.get("type", "offcurve") for s, a, d in children]
	if set(pointTypes) != set(("offcurve")):
		while pointTypes[-1] == "offcurve":
			pointTypes.insert(0, pointTypes.pop(-1))
		segments = []
		for pointType in reversed(pointTypes):
			if pointType != "offcurve":
				segments.append([pointType])
			else:
				segments[-1].append(pointType)
		for segment in segments:
			if len(segment) == 1:
				continue
			segmentType = segment[0]
			offCurves = segment[1:]
			# move and line can't be preceded by off-curves
			if segmentType == "move":
				raise GlifLibError("move can not have an offcurve.")
			elif segmentType == "line":
				raise GlifLibError("line can not have an offcurve.")
			elif segmentType == "curve":
				if len(offCurves) > 2:
					raise GlifLibError("Too many offcurves defined for curve.")
			elif segmentType == "qcurve":
				pass
			else:
				# unknown segement type. it'll be caught later.
				pass

def _buildOutlinePoints(pen, children, formatVersion, identifiers):
	for index, (subElement, attrs, dummy) in enumerate(children):
		# unknown child element of contour
		if subElement != "point":
			raise GlifLibError("Unknown child element (%s) of contour element." % subElement)
		# search for unknown attributes
		if set(attrs.keys()) - pointAttributes:
			raise GlifLibError("Unknown attributes in point element.")
		# search for unknown children
		if len(dummy):
			raise GlifLibError("Unknown child elements in point element.")
		# x and y are required
		x = attrs.get("x", "undefined")
		y = attrs.get("y", "undefined")
		if x is None:
			raise GlifLibError("Required x attribute is missing in point element.")
		if y is None:
			raise GlifLibError("Required y attribute is missing in point element.")
		x = _number(x)
		y = _number(y)
		# type is not required
		segmentType = attrs.get("type", "offcurve")
		if segmentType not in pointTypeOptions:
			raise GlifLibError("Unknown point type: %s" % segmentType)
		if segmentType == "offcurve":
			segmentType = None
		# move can only occur as the first point
		if segmentType == "move" and index != 0:
			raise GlifLibError("A move point occurs after the first point in the contour.")
		# smooth is not required
		smooth = attrs.get("smooth", "no")
		if smooth is not None:
			if smooth not in pointSmoothOptions:
				raise GlifLibError("Unknown point smooth value: %s" % smooth)
		smooth = smooth == "yes"
		if smooth and segmentType not in ("curve", "qcurve"):
			raise GlifLibError("smooth attribute set in a %s point." % segmentType)
		# name is not required
		name = attrs.get("name")
		# identifier is not required but it is not part of format 1
		identifier = attrs.get("identifier")
		if identifier is not None and formatVersion == 1:
			raise GlifLibError("The point identifier attribute is not allowed in GLIF format 1.")
		if identifier is not None:
			if identifier in identifiers:
				raise GlifLibError("The identifier %s is used more than once." % identifier)
			if not identifierValidator(identifier):
				raise GlifLibError("The identifier %s is not valid." % identifier)
			identifiers.add(identifier)
		# try to pass the identifier attribute
		try:
			pen.addPoint((x, y), segmentType=segmentType, smooth=smooth, name=name, identifier=identifier)
		except TypeError:
			pen.addPoint((x, y), segmentType=segmentType, smooth=smooth, name=name)
			raise DeprecationWarning("The addPoint method needs an identifier kwarg. The point's identifier value has been discarded.")

# ---------------------
# Misc Helper Functions
# ---------------------

def _relaxedSetattr(object, attr, value):
	try:
		setattr(object, attr, value)
	except AttributeError:
		pass

def _number(s):
	"""
	Given a numeric string, return an integer or a float, whichever
	the string indicates. _number("1") will return the integer 1,
	_number("1.0") will return the float 1.0.

	>>> _number("1")
	1
	>>> _number("1.0")
	1.0
	>>> _number("a")
	Traceback (most recent call last):
	    ...
	GlifLibError: Could not convert a to an int or float.
	"""
	try:
		n = int(s)
		return n
	except ValueError:
		pass
	try:
		n = float(s)
		return n
	except ValueError:
		raise GlifLibError("Could not convert %s to an int or float." % s)

# -------------------
# Glyph Name Fetching
# -------------------

class _DoneParsing(Exception): pass

def _startElementHandler(tagName, attrs):
	if tagName != "glyph":
		# the top level element of any .glif file must be <glyph>
		raise _DoneParsing(None)
	glyphName = attrs["name"]
	raise _DoneParsing(glyphName)

def _fetchGlyphName(glyphPath):
	# Given a path to an existing .glif file, get the glyph name
	# from the XML data.
	from xml.parsers.expat import ParserCreate

	p = ParserCreate()
	p.StartElementHandler = _startElementHandler
	p.returns_unicode = True
	f = open(glyphPath)
	try:
		p.ParseFile(f)
	except _DoneParsing, why:
		glyphName = why.args[0]
		if glyphName is None:
			raise ValueError, (".glif file doen't have a <glyph> top-level "
					"element: %r" % glyphPath)
	else:
		assert 0, "it's not expected that parsing the file ends normally"
	return glyphName

# ----------------
# Unicode Fetching
# ----------------

def _fetchUnicodes(text):
	# Given GLIF text, get a list of all unicode values from the XML data.
	parser = _FetchUnicodesParser(text)
	return parser.unicodes

class _FetchUnicodesParser(object):

	def __init__(self, text):
		from xml.parsers.expat import ParserCreate
		self.unicodes = []
		self._elementStack = []
		parser = ParserCreate()
		parser.returns_unicode = 0  # XXX, Don't remember why. It sucks, though.
		parser.StartElementHandler = self.startElementHandler
		parser.EndElementHandler = self.endElementHandler
		parser.Parse(text)

	def startElementHandler(self, name, attrs):
		if name == "unicode" and len(self._elementStack) == 1 and self._elementStack[0] == "glyph":
			value = attrs.get("hex")
			value = int(value, 16)
			self.unicodes.append(value)
		self._elementStack.append(name)

	def endElementHandler(self, name):
		other = self._elementStack.pop(-1)
		assert other == name


# --------------
# GLIF Point Pen
# --------------

_transformationInfo = [
	# field name, default value
	("xScale",    1),
	("xyScale",   0),
	("yxScale",   0),
	("yScale",    1),
	("xOffset",   0),
	("yOffset",   0),
]

class GLIFPointPen(AbstractPointPen):

	"""
	Helper class using the PointPen protocol to write the <outline>
	part of .glif files.
	"""

	def __init__(self, xmlWriter, formatVersion=2, identifiers=None):
		if identifiers is None:
			identifiers = set()
		self.formatVersion = formatVersion
		self.identifiers = identifiers
		self.writer = xmlWriter
		self.prevSegmentType = None
		self.prevOffCurveCount = 0

	def beginPath(self, identifier=None, **kwargs):
		attrs = []
		if identifier is not None and self.formatVersion >= 2:
			if identifier in self.identifiers:
				raise GlifLibError("identifier used more than once: %s" % identifier)
			if not identifierValidator(identifier):
				raise GlifLibError("identifier not formatted properly: %s" % identifier)
			attrs.append(("identifier", identifier))
			self.identifiers.add(identifier)
		self.writer.begintag("contour", attrs)
		self.writer.newline()
		self.prevSegmentType = None
		self.prevOffCurveCount = 0

	def endPath(self):
		self.writer.endtag("contour")
		self.writer.newline()
		self.prevSegmentType = None
		self.prevOffCurveCount = 0

	def addPoint(self, pt, segmentType=None, smooth=None, name=None, identifier=None, **kwargs):
		attrs = []
		# coordinates
		if pt is not None:
			for coord in pt:
				if not isinstance(coord, (int, float)):
					raise GlifLibError("coordinates must be int or float")
			attrs.append(("x", str(pt[0])))
			attrs.append(("y", str(pt[1])))
		# segment type
		if segmentType == "move" and self.prevSegmentType is not None:
			raise GlifLibError("move occurs after a point has already been added to the contour.")
		if segmentType in ("move", "line") and self.prevSegmentType == "offcurve":
			raise GlifLibError("offcurve occurs before %s point." % segmentType)
		if segmentType == "curve" and self.prevOffCurveCount > 2:
			raise GlifLibError("too many offcurve points before curve point.")
		if segmentType is not None:
			attrs.append(("type", segmentType))
		else:
			segmentType = "offcurve"
		if segmentType == "offcurve":
			self.prevOffCurveCount += 1
		else:
			self.prevOffCurveCount = 0
		self.prevSegmentType = segmentType
		# smooth
		if smooth:
			if segmentType not in ("curve", "qcurve"):
				raise GlifLibError("can't set smooth in a %s point." % segmentType)
			attrs.append(("smooth", "yes"))
		# name
		if name is not None:
			attrs.append(("name", name))
		# identifier
		if identifier is not None and self.formatVersion >= 2:
			if identifier in self.identifiers:
				raise GlifLibError("identifier used more than once: %s" % identifier)
			if not identifierValidator(identifier):
				raise GlifLibError("identifier not formatted properly: %s" % identifier)
			attrs.append(("identifier", identifier))
			self.identifiers.add(identifier)
		self.writer.simpletag("point", attrs)
		self.writer.newline()

	def addComponent(self, glyphName, transformation, identifier=None, **kwargs):
		attrs = [("base", glyphName)]
		for (attr, default), value in zip(_transformationInfo, transformation):
			if not isinstance(value, (int, float)):
				raise GlifLibError("transformation values must be int or float")
			if value != default:
				attrs.append((attr, str(value)))
		if identifier is not None and self.formatVersion >= 2:
			if identifier in self.identifiers:
				raise GlifLibError("identifier used more than once: %s" % identifier)
			if not identifierValidator(identifier):
				raise GlifLibError("identifier not formatted properly: %s" % identifier)
			attrs.append(("identifier", identifier))
			self.identifiers.add(identifier)
		self.writer.simpletag("component", attrs)
		self.writer.newline()


if __name__ == "__main__":
	import doctest
	doctest.testmod()

	from pprint import pprint
	from robofab.pens.pointPen import PrintingPointPen

	class TestGlyph: pass

	gs = GlyphSet(".")

	def drawPoints(pen):
		pen.beginPath(identifier="my contour")
		pen.addPoint((100, 200), name="foo", identifier="my point")
		pen.addPoint((200, 250), segmentType="curve", smooth=True)
		pen.endPath()
		pen.addComponent("a", (1, 0, 0, 1, 20, 30), identifier="my component")

	glyph = TestGlyph()
	glyph.width = 120
	glyph.unicodes = [1, 2, 3, 43215, 66666]
	glyph.lib = {"a": "b", "c": [1, 2, 3, True]}
	glyph.note = "  hallo!   "
	glyph.image = dict(fileName="foo.png", color="1,1,0,.1")
	glyph.guidelines = [dict(x=100, y=200, angle=144.44)]

	if 0:
		gs.writeGlyph("a", glyph, drawPoints)
		g2 = TestGlyph()
		gs.readGlyph("a", g2, PrintingPointPen())
		pprint(g2.__dict__)
	else:
		s = writeGlyphToString("a", glyph, drawPoints)
		print s
		g2 = TestGlyph()
		readGlyphFromString(s, g2, PrintingPointPen())
		pprint(g2.__dict__)
