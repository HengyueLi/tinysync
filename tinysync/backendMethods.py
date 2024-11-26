"""
Most of the rclone interfacing
"""
import json
import os

import subprocess
import lzma
import time

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import defaultdict
from pathlib import PurePosixPath

from .log import debug, log, MINRCLONE
# from ..deprecated.cli import ConfigError
from .dicttable import DictTable
from . import utils
from .backend.abc import Backend

import hashlib
import threading
import logging
from typing import Callable
import datetime


lock1 = threading.Lock()
lock2 = threading.Lock()
lock3 = threading.Lock()
lock4 = threading.Lock()



class ConfigError(Exception):pass

FILTER_FLAGS = {
    "--include",
    "--exclude",
    "--include-from",
    "--exclude-from",
    "--filter",
    "--filter-from",
    "--files-from",
}


def mkdir(path, isdir=True):
    if not isdir:
        path = os.path.dirname(path)
    try:
        os.mkdir(path)
    except OSError:
        pass


class LockedRemoteError(ValueError):
    pass


class RcloneVersionError(ValueError):
    pass


class _PathEncoder():

    def __init__(self):
        self.CUSTOM_ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyz-_'

    @staticmethod
    def encode_custom_base(data, alphabet):
        """将字节数据编码为自定义基数格式"""
        base = len(alphabet)  # 自定义基数
        num = int.from_bytes(data, 'big')
        
        if num == 0:
            return '0'
        encode = ''
        while num > 0:
            num, rem = divmod(num, base)
            encode = alphabet[rem] + encode
        
        # 处理领导零字节
        zero_char = alphabet[0]
        for byte in data:
            if byte == 0:
                encode = zero_char + encode
            else:
                break
        
        return encode

    @staticmethod
    def decode_custom_base(encoded_string, alphabet):
        """将自定义基数编码字符串解码为字节数据"""
        base = len(alphabet)
        num = 0
        for char in encoded_string:
            num = num * base + alphabet.index(char)
        
        byte_length = (num.bit_length() + 7) // 8
        decoded = num.to_bytes(byte_length, 'big')
        
        return decoded
    
    def encode(self,originStr):
        return self.encode_custom_base( originStr.encode(), self.CUSTOM_ALPHABET )
    
    def decode(self,encodedStr):
        return self.decode_custom_base( encodedStr, self.CUSTOM_ALPHABET   ).decode() 
    

class ParseLink():

    def __init__(self):
        self.encoder = _PathEncoder()
        self.sufix = "_symlin_"

    def encodeLinkToFilename(self,name,target,ltype):
        return f"{name}.{self.encoder.encode(target)}.{ltype}.{self.sufix}" 

    def decodeFilenameToLink(self,filename:str):
        if filename.endswith("."+self.sufix):
            segs = filename.split(".") 
            name,target,ltype,sufix = ".".join(segs[:-3]),segs[-3],segs[-2],segs[-1] 
            return name,self.encoder.decode(target),ltype  
        else:
            return None,None,None
        
    # def isVirtualFile(self,path):
    #     # 判断是一个真实的link对象，只是以filepath存在list中
    #     segs = path.split("/")
    #     filename = segs[-1] 
    #     return filename.endswith("."+self.sufix+"@")
        
    def parsePath_fakefile2realdir(self,path)->tuple[str,str,str]:
        p = path 
        segs = p.split("/") 
        fdir = ( "/".join( segs[:-1] ) ).strip()  
        name,target,ltype = self.decodeFilenameToLink( segs[-1] ) 
        if name is None:
            return None,None,None
        else:
            if len(fdir)>0:
                return fdir+"/"+name,target,ltype 
            else:
                return name,target,ltype 
    
    def parsePath_realdir2fakefile(self,path,target,ltype)->str:
        segs = path.split("/") 
        name = segs[-1]
        fdir = ("/".join(segs[:-1])).strip() 
        fname = self.encodeLinkToFilename(name,target,ltype)  
        if len(fdir) > 0:
            return fdir + "/" + fname
        else:
            return fname

          

def _get_Metadata(path,backend,lock):
    with lock:
        return backend.metaCache.get(path,None)




def util_filter_paths(paths):
    '''
    GPT: 要解决这个问题，即从给定的路径列表中找出一个最小的子集，使得列表中的其他所有路径都是这个子集中某个路径的子路径，
    可以使用以下算法。此算法的目标是确保选择的路径列表尽可能短，同时包含其它所有路径。
    '''
    # 步骤1: 排序路径
    sorted_paths = sorted(paths)
    
    # 步骤2: 筛选路径
    result = []
    for path in sorted_paths:
        if not result:
            result.append(path)
        else:
            # 检查当前路径是否为结果中最后一个路径的子路径
            if not path.startswith(result[-1] + '/'):
                result.append(path)
    
    return result



class PathMethods():

    @staticmethod
    def splitPathAndName(path)->tuple[str,str]:
        segs = path.split("/") 
        name = segs[-1]
        path = "/".join(segs[:-1])
        return path,name

    @staticmethod
    def mergePathAndName(path,name)->str:
        if len(path) ==0:
            return name 
        else:
            return path + "/" + name  



class BackendHandler():

    def __init__(self,backend:Backend,workdir:str,syncConfig:dict):
        self.backend = backend 
        self.linkParser = ParseLink()
        self.linkMode = syncConfig['linkMode']
        self.possibleLinks = set()  
        self.possibleDirs = set() 
        self.possibleFakefiles = set() 
        self.metaCache = dict() 
        self.workdir = workdir
        self.syncConfig=syncConfig
        
    def _clean_linkStore(self):
        self.possibleLinks = set()
        self.possibleDirs = set() 
        self.possibleFakefiles = set() 
        self.metaCache = {"":{"name":"",'type':'d','Size':0,'mtime':0} } #dict() 

    def getRemoteWorkDir(self):
        wd = self.backend.workdir 
        if wd is None:
             wd = ".tinysync"
        return wd 


    def _remoteMove(self,rPathSrc:str,rPathDst:str):
        'move a source (of file or dir) to target path'

        def _normalMove(src,dst):
            if self._isServersideSupport_move():
                return self.backend.remoteMove(src,dst) 
            else:
                log(f"[HIGH COST MOVE]: {rPathSrc} -> {rPathDst}") 

                tmpf = os.path.join(self.workdir,  f"remoteMove{str(time.time())}.f" )
                self.backend.getFile(rPathRemote=rPathSrc, localPath=tmpf) 
                mtime = self.metaCache[rPathSrc]['mtime']
                os.utime(tmpf, (mtime, mtime))
                self.backend.putFile(localPath=tmpf,rPathRemote=rPathDst)

                return 0
        if self.linkMode == 2:
            if (rPathSrc in self.possibleLinks) or (rPathSrc in self.possibleFakefiles):
                # delete source
                if rPathSrc in self.possibleLinks:
                    realSrcPath,target,ltype = self.linkParser.parsePath_fakefile2realdir(rPathSrc)
                    self.backend.deleteFile( realSrcPath ) 
                else:
                    self.backend.deleteFile( rPathSrc )
                # create destination 
                if self._isServersideSupport_symbollink():
                    realDstPath,target,ltype = self.linkParser.parsePath_fakefile2realdir(rPathDst) 
                    self.backend.mklink(realDstPath,target,ltype=='d') 
                else:
                    self.writeEmptyFile(  rPathDst  ) 

                return 0 
            else:
                return _normalMove(rPathSrc,rPathDst)   
        else:
            return _normalMove(rPathSrc,rPathDst)





        
    def _remoteCopy(self,rPathSrc:str,rPathDst:str):
        'copy a source (of file or dir) to target path'

        def _normalCopy(src,dst):
            if self._isServersideSupport_copy():
                return self.backend.remoteCopy(src,dst) 
            else:
                # not concreated
                pass 
                raise Exception("???should not happend!1") 
            
        
        if self.linkMode == 2:
            if (rPathSrc in self.possibleLinks) or (rPathSrc in self.possibleFakefiles):
                # create destination 
                if self._isServersideSupport_symbollink():
                    realDstPath,target,ltype = self.linkParser.parsePath_fakefile2realdir(rPathDst) 
                    self.backend.mklink(realDstPath,target,ltype=='d') 
                else:
                    self.writeEmptyFile(  rPathDst  ) 

                return 0 
            else:
                return _normalCopy(rPathSrc,rPathDst)   
        else:
            return _normalCopy(rPathSrc,rPathDst)


    def _remoteBatchMove(self,pairs:list[tuple[str,str]],retry=1)->int:
        batchDelete = [] 
        mkLinks = set()
        rpairs = []

        if (self.linkMode == 0) or ( self.linkMode == 1 ):
            rpairs = pairs
        elif self.linkMode == 2:
            for (src,dst) in pairs:
                if src in self.possibleLinks:
                    p,t,lt = self.linkParser.parsePath_fakefile2realdir(src) 
                    batchDelete.append(p) 
                    mkLinks.add( dst )
                if src in self.possibleFakefiles:
                    batchDelete.append(src) 
                    mkLinks.add( dst )
                if (src not in self.possibleLinks) and (src not in self.possibleFakefiles):
                    rpairs.append((src,dst)) 
        else:
            raise Exception(f"unexpected linkMode={self.linkMode}")
        

        self._remoteBatchDelete(batchDelete)

        if self.linkMode == 2 and (not self._isServersideSupport_symbollink()):
            for l in mkLinks:
                self.writeEmptyFile(l) 
        
        if self.linkMode == 2 and self._isServersideSupport_symbollink():
            for l in mkLinks:
                p,t,lt = self.linkParser.parsePath_fakefile2realdir(l)
                self.backend.mklink(p,t,lt=='d') 


        if not self._isServersideSupport_batchMove():
            # not concreated
            returnCode = 0 
            maxTry = 1 + retry
            for (src,dst) in rpairs: 
                code = -1
                c = 0  
                while code !=0:
                    code = self._remoteMove(src,dst) 
                    c += 1 
                    if c == maxTry:
                        logging.error(f"cannot move with additional retry {retry} times") 
                        returnCode += 1 
                        break 
            return returnCode 
        else:
            return self.backend.remoteBatchMove(rpairs)
        

    def _remoteBatchCopy(self,pairs:list[tuple[str,str]],retry=1)->int:
        mkLinks = set()
        rpairs = []

        if (self.linkMode == 0) or ( self.linkMode == 1 ):
            rpairs = pairs
        elif self.linkMode == 2:
            for (src,dst) in pairs:
                if (src in self.possibleLinks) or (src in self.possibleFakefiles):
                    mkLinks.add( dst )
                else:
                    rpairs.append((src,dst)) 
        else:
            raise Exception(f"unexpected linkMode={self.linkMode}")

        if self.linkMode == 2 and (not self._isServersideSupport_symbollink()):
            for l in mkLinks:
                self.writeEmptyFile(l) 
        
        if self.linkMode == 2 and self._isServersideSupport_symbollink():
            for l in mkLinks:
                p,t,lt = self.linkParser.parsePath_fakefile2realdir(l)
                self.backend.mklink(p,t,lt=='d') 

        if not self._isServersideSupport_batchCopy():
            # not concreated
            returnCode = 0 
            maxTry = 1 + retry
            for (src,dst) in rpairs: 
                code = -1
                c = 0  
                while code !=0:
                    code = self._remoteCopy(src,dst) 
                    c += 1 
                    if c == maxTry:
                        logging.error(f"cannot copy with additional retry {retry} times") 
                        returnCode += 1 
                        break 
            return returnCode 
        else:
            return self.backend.remoteBatchCopy(rpairs)

    def _remoteBatchDelete(self,items:list[str])->int:
        remoteBatchDelete = self.backend.remoteBatchDelete.__func__ 
        if remoteBatchDelete is Backend.remoteBatchDelete:
            # not concreated
            for i in items:
                self.backend.deleteFile(i) 
            return 0
        else:
            return self.backend.remoteBatchDelete(items)

    def _RecursivelylistFilesInPath(self,rpath:str,mtime:bool=True,hash=False,pathFilter=None) ->  dict :
        """recursively list files in a path 

        Args:
            rpath (str): relative path , example: rpath = "this/path", rpath = "" 
            mtime (bool, optional): contains mtime or not.
            hash (bool, optional): contains hash value or not.
            linkMode (int): 0 transparent for slink
                            1 treat the slink as a normal dir (DANGEROUS) 
                            2 keep the slink, must be stored as speacial filed on backends that not support symbolic link. 强行把符号连接表示成为普通文件

        Returns:
            list[ dict ]: dict = { "Path":str, "Size":int, "mtime":float }
                          if hash, dict["Hashes"] = {...}, for example dict["Hashes"]={"sha1":"66c..."}         

        """   
        rpath = rpath.strip()
        if pathFilter is not None:
            if not pathFilter(rpath):
                return {} 
        linkMode = self.linkMode
        if rpath.endswith("/"):
            rpath = rpath[:1]
        if len(rpath) == 0:
            prefix = "" 
        else:
            prefix = rpath + "/"
        res = {}
        currentListDir = self.backend.listPath(rpath=rpath,mtime=mtime,hash=hash) 
        for t in currentListDir:
            rp = prefix + t['name']
            self.metaCache[rp] = t 
            if t['type'] == 'd':
                self.possibleDirs.add(rp)
                res |= self._RecursivelylistFilesInPath(rpath=rp,hash=hash,pathFilter=pathFilter) 
            elif t['type'] == 'f': 
                _path = prefix + t['name']
                t['Path'] = _path
                del t['type']
                del t['name']
                res[_path] = t   
                if linkMode == 2: 
                    p1,target,ltype = self.linkParser.parsePath_fakefile2realdir(_path)
                    if p1 is not None:
                        self.possibleFakefiles.add(_path) 
                        t['Size'] = 1 
                        t['mtime'] = 1700000000
            elif t['type'] == 'l':
                if linkMode ==0:
                    self.possibleLinks.add(rp)
                elif linkMode == 1:
                    if t['ltype'] == 'd':
                        res |= self._RecursivelylistFilesInPath(rpath=rp,hash=hash,pathFilter=pathFilter) 
                    elif t['ltype'] == 'f':
                        self.possibleLinks.add(rp) 
                    else:
                        raise Exception("?????")
                elif linkMode == 2:
                    fakeName = self.linkParser.encodeLinkToFilename(name=t['name'],target=t['target'],ltype=t['ltype'])
                    _path = prefix + fakeName
                    t['Path'] = _path   
                    t['Size'] = 1
                    t['mtime'] = 1700000000
                    # del t['type']
                    del t['name']
                    res[_path] = t  
                    self.possibleLinks.add(_path)
                else:
                    raise Exception(f"unknow linkMode = {linkMode}")
            else:
                raise Exception(f"unknow target type = {t['type']} for [{t['Path']}]") 
        #--- print log ---------------------
        pathObj = PurePosixPath(rpath)
        fdir = str(pathObj).replace(".",'') 
        segs = fdir.split("/")
        if len(segs) == 2:
            log(f" scaned [{self.backend.getSyncPath()}] {rpath.ljust(25, ' ')}: {len(res)} items")
        return res 

    def _recursiveListMeta(self,path):# support real types
        # self.linkMode
        rpath = path.strip()
        if rpath.endswith("/"):
            rpath = rpath[:1]
        if len(rpath) == 0:
            prefix = "" 
        else:
            prefix = rpath + "/"
        resDirs,resFiles = [], []  
        currentListDir = self.backend.listPath(rpath=rpath) 
        for t in currentListDir:
            rp = prefix + t['name']
            if t['type'] == 'd':
                subDirs,subFiles = self._recursiveListMeta(path=rp) 
                resDirs += subDirs 
                resFiles += subFiles 
            else: 
                _path = prefix + t['name']
                t['Path'] = _path
                resFiles.append(t) 
        return resDirs,resFiles 

    def _purgePath(self,path:str,deleteDirs=False):
        if self._isServersideSupport_Purge():
            self.backend.purge(path) 
        else:
            dirs,files = self._recursiveListMeta(path) 
            self._remoteBatchDelete(files) 
            if deleteDirs:
                for d in reversed(dirs):
                    self.backend.rmdir(d) 




    
    def _isServersideSupport_move(self):
        '''Return whether or not the remote supports  server-side move'''
        return not (self.backend.remoteMove.__func__ is Backend.remoteMove)
    
    def _isServersideSupport_copy(self):
        '''Return whether or not the remote supports  server-side copy'''
        return not (self.backend.remoteCopy.__func__ is Backend.remoteCopy)
    
    def _isServersideSupport_batchMove(self):
        cacheKey = "__SUPPORT_BATCHMOVE__" 
        if not hasattr(self,cacheKey):
            isSupport = not ( self.backend.remoteBatchMove.__func__ is Backend.remoteBatchMove ) 
            setattr(self,cacheKey,isSupport)
        return getattr(self,cacheKey)         
        

    def _isServersideSupport_symbollink(self):
        cacheKey = "__SUPPORT_SYMBOLLINK__" 
        if not hasattr(self,cacheKey):
            isSupport = not ( self.backend.mklink.__func__ is Backend.mklink ) 
            setattr(self,cacheKey,isSupport)
        return getattr(self,cacheKey) 
    
    def _isServersideSupport_batchCopy(self):
        cacheKey = "__SUPPORT_BATCHCOPY__" 
        if not hasattr(self,cacheKey):
            isSupport = not ( self.backend.remoteBatchCopy.__func__ is Backend.remoteBatchCopy ) 
            setattr(self,cacheKey,isSupport)
        return getattr(self,cacheKey) 
    
    def _isServersideSupport_Purge(self):
        cacheKey = "__SUPPORT_PURGE__" 
        if not hasattr(self,cacheKey):
            isSupport = not ( self.backend.purge.__func__ is Backend.purge ) 
            setattr(self,cacheKey,isSupport)
        return getattr(self,cacheKey) 
    
    def writeEmptyFile(self,path):
        new_mtime = 1700000000
        tmf = os.path.join(self.workdir, f"wEmp{str(time.time())}.tmpf0" )
        with open(tmf,'w') as fi:
            pass 
        os.utime(tmf, (new_mtime, new_mtime))
        # 将临时文件上传到 B 服务器对应的路径
        self.backend.putFile(localPath=tmf, rPathRemote=path)         

    def writeContentFile(self,path,content):
        new_mtime = 1700000000
        tmf = os.path.join(self.workdir, f"wEmp{str(time.time())}.tmpf0" )
        with open(tmf,'w') as fi:
            fi.write(content)
        os.utime(tmf, (new_mtime, new_mtime))
        # 将临时文件上传到 B 服务器对应的路径
        self.backend.putFile(localPath=tmf, rPathRemote=path) 

    def readContentFile(self,rpath):
        tmf = os.path.join(self.workdir, f"wCmp{str(time.time())}.tmpf0" )
        self.backend.getFile(rpath,tmf) 
        with open(tmf,'r') as f:
            return f.read()
        
    def readjsonxz(self,path):
        tmf = os.path.join(self.workdir, f"readjsonxz{str(time.time())}.xz" )
        rcode = self.backend.getFile(path,tmf) 
        if rcode == 0:
            with lzma.open(tmf) as file:
                jsObj = json.load(file)
            if os.path.exists(tmf):
                os.remove(tmf) 
            return 0,jsObj
        elif rcode == -1:
            return -1,[]
        else:
            return None,[]


    def writejsonxz(self,rpath,jsonObj):
        tmf = os.path.join(self.workdir, f"readjsonxz{str(time.time())}.xz" )
        with lzma.open(tmf, "wt", encoding='utf-8') as file:
            json.dump(jsonObj, file, ensure_ascii=False)
        self.backend.putFile(tmf,rpath)





    def _createFakeLinkFile(self,path:str,target:str,isDir:bool):
        fp = self.linkParser.parsePath_realdir2fakefile(path,target,"d" if isDir else "f") 
        self.writeEmptyFile( fp )
            

    def getPushedListPath(self,AB):
        workdir = self.getRemoteWorkDir()
        dst = utils.pathjoin(workdir, f"{AB}-{self.syncConfig['name']}_fl.json.xz")
        return dst       









def _makedirsIfNeeded(dirPath,backend:BackendHandler,lock):
    dirMeta = _get_Metadata(dirPath,backend,lock)
    if dirMeta is None:
        fatherDir,fname = PathMethods.splitPathAndName(dirPath)
        if len(fatherDir) > 0:
            _makedirsIfNeeded(fatherDir,backend,lock)
        backend.backend.mkdir(dirPath) 
        with lock:
            # metaDictCache[dirPath]=backend.backend.getMetadata(dirPath)
            # metaDictCache[dirPath] = { 'type':'d', 'size':'', 'mtime':''  }
            backend.metaCache[dirPath] = { 'type':'d', 'size':0, 'mtime':0  }
    else:
        if dirMeta['type'] !='d':
            raise Exception(f"Tried to mkdir path=[{dirPath}]. However, this path exists and is not a directory!")
    
def _mkdirFilePathAllowed(rpath,backend,lock):
    # rpath is a file path, check its father dir exist. If not, mkdir recursively 
    father,fname = PathMethods.splitPathAndName(rpath)
    if len(father) == 0:
        return 
    _makedirsIfNeeded(father,backend,lock)  



def copyFileWithoutCheckFatherDir(filePath:str,srcBackend:BackendHandler,dstBackend:BackendHandler,localWorkDir):
    tp = os.path.join(localWorkDir,hashlib.md5(filePath.encode('utf-8')).hexdigest()[:8])
    srcBackend.backend.getFile(rPathRemote=filePath, localPath=tp)
    dstBackend.backend.putFile(localPath=tp, rPathRemote=filePath)
    srcBackend.backend.cleanQueue() 
    dstBackend.backend.cleanQueue()

def copyFilesWithoutCheckFatherDir(filePaths:list,srcBackend:BackendHandler,dstBackend:BackendHandler,localWorkDir):
    syncConfig = srcBackend.syncConfig
    maxCacheSize = int(syncConfig['cacheSizeMB']*1024*1024)
    directory_dict = defaultdict(list)

    hashName = {}


    for filePath in filePaths:
        _path = Path(filePath) 
        directory = _path.parent  
        directory_dict[str(directory)].append(filePath)  
        hashName[filePath] = os.path.join(localWorkDir,hashlib.md5(filePath.encode('utf-8')).hexdigest()[:8]) 

    sizeUsed = 0
    cachedPath = []
    allFiles = []
    for _, files in directory_dict.items():
        allFiles += files
    L = len(allFiles)
    for i,filePath in enumerate(allFiles):
        try:
            srcBackend.backend.getFile(rPathRemote=filePath, localPath=hashName[filePath])
            cachedPath.append(filePath)
            fsize = srcBackend.metaCache[filePath]['Size']
            sizeUsed += fsize
        except:
            log(f"ERROR: faild to download {filePath}, Maybe source files are changed")
        if (sizeUsed > maxCacheSize) or ( i == L-1 ):
            log("free cached data...")
            try:
                log("concrete downloading...")
                srcBackend.backend.cleanQueue() 
            except:
                log(f"ERROR: cannot cleanQueue on [{srcBackend.backend.getSyncPath()}]. Maybe source files are changed. However, try to ignore this error")
            for p in cachedPath: 
                if os.path.exists(hashName[p]):
                    dstBackend.backend.putFile(localPath=hashName[p], rPathRemote=p) 
                else:
                    log(f"failed to tranfor [{p}], maybe source files changed.")
            log("concrete uploading...")
            dstBackend.backend.cleanQueue()
            for p in cachedPath:
                lp = hashName[p]
                if os.path.exists(lp): 
                    os.remove(lp)      
            sizeUsed = 0 
            cachedPath = []
    srcBackend.backend.cleanQueue()
    dstBackend.backend.cleanQueue()


        

def copyFile_firstConsiderLink(filePath:str,path:str,target:str,isDir:bool,dstBackend:BackendHandler,lock):
    if dstBackend._isServersideSupport_symbollink():
        dsm = _get_Metadata(path,dstBackend,lock)
        if dsm is None: 
            _mkdirFilePathAllowed(path,dstBackend,lock) 
            dstBackend.backend.mklink(rPathRemote=path,target=target,isDir=isDir)
            dstBackend.metaCache[path] = {'type':'l','ltype':'d' if isDir else 'f', 'size':1, 'mtime':1700000000 }
        else:
            if (dsm['type'] == 'f') or ( dsm['type'] == 'l' and ( dsm['target'] != target)  ):
                dstBackend.backend.deleteFile(path) 
                dstBackend.backend.mklink(rPathRemote=path,target=target,isDir=isDir) 
                del dstBackend.metaCache[path] 
            elif dsm['type'] == 'l' and ( dsm['target'] == target):
                # link exists, and need not to create 
                log("target link exist, ignore copy") 
            elif dsm['type'] == 'd':
                raise Exception("why this happend?1") 
            else:
                print("dsm=",dsm,"target=",target)
                raise Exception(f"why yhis happend? error when copy link: {filePath}")
    else:
        dsm = _get_Metadata(filePath,dstBackend,lock)
        if dsm is None:
            _mkdirFilePathAllowed(filePath,dstBackend,lock) 
            dstBackend._createFakeLinkFile(path,target,isDir) 
            dstBackend.metaCache[filePath] = {"type":'f','size':1, 'mtime':1700000000}




def copySingleFileSizeOnly(filePath:str,srcBackend:BackendHandler,dstBackend:BackendHandler,localWorkDir):
    # check if it is link
    if dstBackend.linkMode == 2:
        # support slink at remote 
        path,target,lt = dstBackend.linkParser.parsePath_fakefile2realdir(filePath)
        if path is not None:
            copyFile_firstConsiderLink(filePath,path,target,lt=='d',dstBackend,lock2)
            return  
    meta_dst = _get_Metadata(filePath,dstBackend,lock2) 
    if meta_dst is None:
        fatherDir = "/".join(filePath.split("/")[:-1]) 
        _makedirsIfNeeded(fatherDir,dstBackend,lock2) 
    else:
        meta_src = _get_Metadata(filePath,srcBackend,lock1) 
        if meta_dst['Size'] == meta_src['Size']:
            return # no need copy
    copyFileWithoutCheckFatherDir(filePath,srcBackend,dstBackend,localWorkDir)

def copyManyFile_SizeOnly(filePaths:str,srcBackend:BackendHandler,dstBackend:BackendHandler,localWorkDir):
    considerLinkFirst = []
    notLinks = []
    if dstBackend.linkMode == 2:
        # support slink at remote 
        for filePath in filePaths:
            path,target,lt = dstBackend.linkParser.parsePath_fakefile2realdir(filePath)
            if path is None:
                notLinks.append(filePath)
            else:
                considerLinkFirst.append( (filePath,path,target,lt=='d') )
    else:
        notLinks = filePaths 
    for i in considerLinkFirst:
        copyFile_firstConsiderLink(*i,dstBackend,lock2)

    actionPath = []
    for filePath in notLinks:
        meta_dst = _get_Metadata(filePath,dstBackend,lock2) 
        if meta_dst is None:
            fatherDir = "/".join(filePath.split("/")[:-1]) 
            _makedirsIfNeeded(fatherDir,dstBackend,lock2) 
            actionPath.append(filePath)
        else:
            meta_src = _get_Metadata(filePath,srcBackend,lock1) 
            if meta_dst['Size'] != meta_src['Size']:
                actionPath.append(filePath)
    copyFilesWithoutCheckFatherDir(actionPath,srcBackend,dstBackend,localWorkDir)


def copySingleFileHash(filePath:str,srcBackend:BackendHandler,dstBackend:BackendHandler,localWorkDir):
    # sharedStore: path -> metadata
    if dstBackend.linkMode == 2:
        # support slink at remote 
        path,target,lt = dstBackend.linkParser.parsePath_fakefile2realdir(filePath)
        if path is not None:
            copyFile_firstConsiderLink(filePath,path,target,lt=='d',dstBackend,lock2)
            return  
    def isHashSame(hashsrc:dict,hashdst:dict):
        # 1 for yes, 0 for no, 2 for not supported 
        for hashType in hashsrc:
            if hashType in hashdst:
                if hashsrc[hashType] == hashdst[hashType]:
                    return 1
                else:
                    return 0 
        return 2 
    meta_dst = _get_Metadata(filePath,dstBackend,lock2) 
    if meta_dst is None:
        fatherDir = "/".join(filePath.split("/")[:-1]) 
        _makedirsIfNeeded(fatherDir,dstBackend,lock2) 
    else:
        meta_src = _get_Metadata(filePath,srcBackend,lock1) 
        hashes_src = meta_src.get('Hashes',{})
        hashes_dst = meta_dst.get('Hashes',{})
        check = isHashSame(hashes_src,hashes_dst) 
        if check == 1:
            return # no need copy 
        elif check == 2:
            # checksum not supported 
            if meta_dst['Size'] == meta_src['Size']:
                return # no need copy 
    copyFileWithoutCheckFatherDir(filePath,srcBackend,dstBackend,localWorkDir)


def copySingleFileHashNotSupport(filePath:str,srcBackend:BackendHandler,dstBackend:BackendHandler,localWorkDir):
    # sharedStore: path -> metadata
    if dstBackend.linkMode == 2:
        # support slink at remote 
        path,target,lt = dstBackend.linkParser.parsePath_fakefile2realdir(filePath)
        if path is not None:
            copyFile_firstConsiderLink(filePath,path,target,lt=='d',dstBackend,lock2)
            return   
    meta_dst = _get_Metadata(filePath,dstBackend,lock2) 
    if meta_dst is None:
        fatherDir = "/".join(filePath.split("/")[:-1]) 
        _makedirsIfNeeded(fatherDir,dstBackend,lock2) 
    else:
        meta_src = _get_Metadata(filePath,srcBackend,lock1) 
        if meta_dst['Size'] == meta_src['Size']:
            if meta_dst['mtime'] > meta_src['mtime']:
                return # no need copy 
    copyFileWithoutCheckFatherDir(filePath,srcBackend,dstBackend,localWorkDir)
    


class FindSubPath():
    '''find sub-path of a given path'''

    # GPT generated
    def __init__(self,existLinks):
        self.existLinks = existLinks

    def findSubPath(self,dirPath):
        '''find sub-path of a given path'''
        return self._find_subpaths(self.existLinks,dirPath)

    @staticmethod
    def is_subpath(base_path, candidate_path):
        # 分割路径成部分
        base_parts = base_path.split('/')
        candidate_parts = candidate_path.split('/')
        
        # 如果候选路径组件数量小于基础路径，它不可能是子路径
        if len(candidate_parts) <= len(base_parts):
            return False

        # 比较基础路径的每个组件是否与候选路径对应组件匹配
        return all(base == candidate for base, candidate in zip(base_parts, candidate_parts))

    @classmethod
    def _find_subpaths(cls,paths, P1):
        # 返回所有P1的子路径
        return [path for path in paths if cls.is_subpath(P1, path)]



class Rclone:


    def __init__(self, backendA:BackendHandler, backendB:BackendHandler,nowStrTag:str,syncConfig,workdir=None):
        
        self.nowStrTag = nowStrTag

        self.syncConfig = syncConfig

        
        self.backend = {"A":backendA,"B":backendB} 
        self.add_args = []  # logging, etc


        tmpdir = workdir

        self.tmpdir = tmpdir#config.tempdir

        try:
            os.makedirs(self.tmpdir)
        except OSError:
            pass




        # self.backup_path, self.backup_path0 = {}, {}
        # for AB in "AB":
        #     workDir = self.getWorkDirFromBackend(self.backend[AB])
        #     self.backup_path0[AB] = f"backups/{self.nowStrTag}_{self.syncConfig['name']}_{AB}"  # really only used for top level non-workdir backups with delete
        #     self.backup_path[AB] = utils.pathjoin(getattr(config, f"workdir{AB}"), self.backup_path0[AB])



    def checkAndMakeRemoteWorkdir(self):
        self.backup_path_re = {}
        self.backup_path = self.backup_path_re
        for AB in "AB":
            workDir = self.getWorkDirFromBackend(self.backend[AB])

            try:
                listWorkDir = self.backend[AB].backend.listPath(workDir) 
                for item in listWorkDir:
                    _path = workDir + "/" + item['name'] 
                    self.backend[AB].metaCache[_path] = item 
            except:
                pass 

            # mkdir: workdir
            _makedirsIfNeeded(workDir,self.backend[AB],lock1)

            # mkdir: log 
            _makedirsIfNeeded(self.getLogsDir(AB) ,self.backend[AB],lock1)

            # mkdir: LOCK 
            _makedirsIfNeeded(self.getLockDir(AB) ,self.backend[AB],lock1)

            # mkdir: backup_path_re
            backupDir = self.getBackupDir(AB) 
            self.backup_path_re[AB] = utils.pathjoin(backupDir,f"{self.nowStrTag}_{self.syncConfig['name']}_{AB}")
            _makedirsIfNeeded(backupDir,self.backend[AB],lock1)




    def clean_link_store(self):
        self.backend['A']._clean_linkStore()
        self.backend['B']._clean_linkStore()


    def getWorkDirFromBackend(self,backend:BackendHandler):
        return backend.getRemoteWorkDir() 

    def getRemoteWorkDir(self,AB):
        return self.getWorkDirFromBackend(self.backend[AB])

    def getLogsDir(self,AB):
        return self.getRemoteWorkDir(AB) + "/" + "logs"

    def getLockDir(self,AB):
        return self.getRemoteWorkDir(AB) + "/" + "LOCK"

    def getBackupDir(self,AB):
        return self.getRemoteWorkDir(AB) + "/" + "backup"
    
    def cleanWorkData(self,AB,timestamp:int):
        'delete all work-dir-data before <timestamp>'
        logDir = self.getLogsDir(AB) 
        backupDir = self.getBackupDir(AB) 
        ''''''
        deleteDirPath = []
        deletefilePath = [] 
        # clean log
        logList = self.backend[AB].backend.listPath( logDir )
        for item in logList:
            try:
                dtStr = item['name'].split('.log')[0].split(self.syncConfig['name']+"_")[1] 
                ts = datetime.datetime.strptime(dtStr,"%Y-%m-%dT%H%M%S").timestamp()
                if ts < timestamp:
                    _p = logDir + "/" + item['name']
                    deletefilePath.append(_p) 
                    log(f"delete expired log: {item['name']}")
            except:
                pass 
        # clean backup 
        bkList = self.backend[AB].backend.listPath( backupDir )
        for item in bkList:
            try:
                dtStr = item['name'].split(f"_{self.syncConfig['name']}_{AB}")[0] 
                ts = datetime.datetime.strptime(dtStr,"%Y-%m-%dT%H%M%S").timestamp()
                if ts < timestamp:
                    _p = backupDir + "/" + item['name']
                    deleteDirPath.append(_p) 
                    log(f"delete expired backup: {item['name']}")
            except:
                pass 
        self.backend[AB]._remoteBatchDelete( deletefilePath ) 
        for d in deleteDirPath:
            self.backend[AB]._purgePath(d) 
        

    # def getPushedListPath(self,AB):
    #     backend = self.backend[AB]
    #     workdir = self.getWorkDirFromBackend(backend)  
    #     dst = utils.pathjoin(workdir, f"{AB}-{self.syncConfig['name']}_fl.json.xz")
    #     return dst       


        

    def push_file_list(self, filelist, remote=None):
        # backend = self.backend[remote]
        # # remote = getattr(config, f"remote{AB}")
        # workdir = self.getWorkDirFromBackend(backend)
        # # workdir = getattr(config, f"workdir{AB}")

        # dst = utils.pathjoin(workdir, f"{remote}-{self.syncConfig['name']}_fl.json.xz")
        dst = self.backend[remote].getPushedListPath(remote) 

        tmf = os.path.join( self.tmpdir, f"{remote}_pushfilelist_"+ str(int(time.time())) + ".xz" )
        with lzma.open(tmf, "wt", encoding='utf-8') as file:
            json.dump(list(filelist), file, ensure_ascii=False)
        self.backend[remote].backend.putFile(localPath=tmf,rPathRemote=dst)
        # print(tmf,"->",dst,) 
        # _=input(" pause ")
        os.remove(tmf)



    def pull_prev_list(self, *, remote=None):
        AB = remote
        backend = self.backend[AB] 
        # workdir = self.getWorkDirFromBackend(backend)
        # src = utils.pathjoin(workdir, f"{AB}-{self.syncConfig['name']}_fl.json.xz")
        src = backend.getPushedListPath(AB) 

        tmf = os.path.join( self.tmpdir, f"{AB}_pullprevlist"+ str(int(time.time())) + ".xz" )
        rcode = backend.backend.getFile(rPathRemote=src,localPath=tmf)
        if rcode == 0:
            with lzma.open(tmf) as file:
                return json.load(file)
        elif rcode == -1:
            log(f"No previous list on {AB}. Reset state")
            return []
        else:
            log(f"WARNING: Unexpected rclone return. Resetting state in {AB}")
            log(f"WARNING: Missing previous state in {AB}. Resetting")
            return [] 


    def file_list(self, *, prev_list=None, remote=None)->tuple[DictTable,DictTable]:
        """
        Get both current and previous file lists. If prev_list is
        set, then it is not pulled.

        Options:
        -------
        prev_list (list or DictTable)
            Previous file list. Specify if it is already known

        remote
            A or B


        It will decide if it needs hashes and whether to reuse them based
        on the config.
        """
        AB = remote


        # config = self.config

        
        # remote = getattr(config, f"remote{AB}")

        backend = self.backend[AB]

        compute_hashes = "hash" in [self.syncConfig['compare'], backend.backend.renames] 
        reuse = compute_hashes and backend.backend.reuse_hashes

    


        if compute_hashes and not reuse:
            isNeed_Hash = True 
        else:
            isNeed_Hash = False

        if not self.syncConfig['always_get_mtime'] and not (
            self.syncConfig['compare'] == "mtime"
            or backend.backend.renames == "mtime"
            or self.syncConfig['conflict_mode'] in ("newer", "older")
        ):
            # cmd.append("--no-modtime")
            isNeed_modtime = False 
        else:
            isNeed_modtime = True 


        workdirName = self.getWorkDirFromBackend(backend=backend)
        lockdirName = utils.pathjoin(workdirName,'LOCK')


        def is_self_or_inside(subpath: str,  fatherpath:str) -> bool:
            return subpath == fatherpath or subpath.startswith(f"{fatherpath}/")


        def notInsideSubDirsInWorkdir(path):
            return (not is_self_or_inside(subpath=path,fatherpath=workdirName)) # or path == workdirName <- 不能listworkdir， preListFile 会被当成文件更新

        def isInsideLockdir(path):
            return is_self_or_inside(subpath=path,fatherpath=lockdirName)

        # 1. outside workdir 
        # 2. inside lock dir 
        metaOutsideWorkdir = backend._RecursivelylistFilesInPath(rpath='',hash=isNeed_Hash,mtime=isNeed_modtime,pathFilter=notInsideSubDirsInWorkdir) 
        try:
            metaInsideLockdir = backend._RecursivelylistFilesInPath(rpath=lockdirName,hash=isNeed_Hash,mtime=isNeed_modtime,pathFilter=isInsideLockdir) 
        except:
            metaInsideLockdir ={} 
        meta = metaOutsideWorkdir | metaInsideLockdir
        files = [f for _,f in meta.items()]




        # Make them DictTables
        files = DictTable(files, fixed_attributes=["Path", "Size", "mtime"])

        debug(f"{AB}: Read {len(files)}")

        if self.syncConfig['reset_state']:
            debug(f"Reset state on {AB}")
            prev_list = []
        else:
            prev_list = self.pull_prev_list(remote=AB)

        if not isinstance(prev_list, DictTable):
            prev_list = DictTable(prev_list, fixed_attributes=["Path", "Size", "mtime"])


        if not compute_hashes or isNeed_Hash:
            return files, prev_list
        
        # 能执行下面内容的条件是 config中 reuse = True 同时  compute_hash=True   

        # update with prev if possible and then get the rest
        not_hashed = []
        updated = 0

        # 从prev_list中更新 curr_list 的 hash值。
        # 因为执行到这里isNeed_Hash = False，所以curr_list中没有hash值
        for file in files:
            prev = prev_list[ {k: file[k] for k in ["Size", "mtime", "Path"]} ]  # Will not find if no mtime not in remote
            if ( (not prev) or ("Hashes" not in prev) or (not prev.get("mtime", None)) ):
                # 如果没找到，或者前值中无Hashes，或者无mtime
                not_hashed.append(file["Path"])
            else:
                updated += 1
                file["Hashes"] = prev["Hashes"]


        # for file in files:  # size,mtime,filename
        #     # 对于 curr 中的file, 找到其再 prev_list 中的值
        #     prev = prev_list[ {k: file[k] for k in ["Size", "mtime", "Path"]} ]  # Will not find if no mtime not in remote
        #     # 如果没找到，或者前值中无Hashes，或者无mtime
        #     if ( not prev or "Hashes" not in prev or not prev.get("mtime", None) ):  # or '_copied' in prev: # Do not reuse a copied hash in case of incompatability
        #         not_hashed.append(file["Path"])
        #         continue
        #     updated += 1
        #     file["Hashes"] = prev["Hashes"]


        if len(not_hashed) == 0:
            debug(f"{AB}: Updated {updated}. No need to fetch more")
            return files, prev_list
        debug(f"{AB}: Updated {updated}. Fetching hashes for {len(not_hashed)}")



        not_hashed = [item.rstrip('/') for item in not_hashed]
        
        def contains_notHashed(path):
            for t in not_hashed:
                if is_self_or_inside(subpath=t,fatherpath=path):
                    return True 
            return False
        
        updated = backend._RecursivelylistFilesInPath(rpath="",hash=True,pathFilter=contains_notHashed)
        updated = [ i for _,i in updated.items()]


        for file in updated:
            if "Hashes" in file:
                files[{"Path": file["Path"]}]["Hashes"] = file["Hashes"]

        debug(f"{AB}: Updated hash on {len(updated)} files")

        return files, prev_list
    



    def delete_backup_move(self, remote, dels, backups, moves):
        """
        Perform deletes, backups and moves. Same basic codes but with different
        reporting. If moves, files are (src,dest) tuples.
        """
        ## Optimization Notes
        #
        # Note: This was previously heavily optimized to avoid overlapping remotes.
        #       However, as of 1.59.0, this is no longer needed and these optimizations
        #       have been undone.
        #
        # rclone is faster if you can do many actions at once. For example, to delete
        # files, it is faster to do `delete --files-from <list-of-files>`.
        #
        # NOTE: The order here is important!
        #
        #     Delete w/ backup: Depends on the remote and the workdir settings
        #       Use `move --files-from` (ability added at 1.59.0)
        #
        #     Moves:
        #       When the file name itself (leaf) changes, we must just do `moveto` calls.
        #       Otherwise, we optimize moves when there there is more than one moved
        #       file at a base directory such as when a directory is moved.
        #       Note: we do NOT do directory moves but this is faster than moveto calls!
        #
        #       Consider:
        #
        #         "A/deep/sub/dir/file1.txt" --> "A/deeper/sub/dir/file1.txt"
        #         "A/deep/sub/dir/file2.txt" --> "A/deeper/sub/dir/file2.txt"
        #
        #       The names ('file1.txt' and 'file2.txt') are the same and there are two
        #       moves from "A/deep" to "A/deeper". Therefore, rather than call moveto
        #       twice, we do:
        #
        #         rclone move "A/deep" "A/deeper" --files-from files.txt
        #
        #       Where 'files.txt' is:
        #          sub/dir/file1.txt
        #          sub/dir/file2.txt"
        #
        #     Backups:
        #       Use the `copy/move --files-from`
        #
        #     Delete w/o backup
        #       Use `delete --files-from`
        #
        # References:
        #
        # https://github.com/rclone/rclone/issues/1319
        #   Explains the issue with the quote:
        #
        #   > For a remote which doesn't it has to move each individual file which might
        #   > fail and need a retry which is where the trouble starts...
        #
        # https://github.com/rclone/rclone/issues/1082
        #   Tracking issue. Also references https://forum.rclone.org/t/moving-the-contents-of-a-folder-to-the-root-directory/914/7

        AB = remote
        # remote = getattr(config, f"remote{AB}")
        backend = self.backend[AB]
        nActionThreads = self.syncConfig['action_threads']

        cmd0 = [None]  # Will get set later
        cmd0 += ["-v", "--stats-one-line", "--log-format", ""]
        # We know in all cases, the dest doesn't exists. For backups, it's totally new and
        # for moves, if it existed, it wouldn't show as a move. So never check dest,
        # always transfer, and do not traverse
        cmd0 += ["--no-check-dest", "--ignore-times", "--no-traverse"]
        cmd0 += ()



        dels = dels.copy()
        moves = moves.copy()
        backups = backups.copy()  # Will be appended so make a new copy

        if self.syncConfig['backup']:
            dels_back = dels
            dels_noback = []
        else:
            dels_back = []
            dels_noback = dels

        debug(AB, "dels_back", dels_back)
        debug(AB, "dels_noback", dels_noback)
        debug(AB, "moves", moves)


        pairs = []
        for p in dels_back:
            src = p 
            dst = utils.pathjoin( self.backup_path_re[AB], p )
            pairs.append(  ( src,dst )  ) 
            _mkdirFilePathAllowed(dst,backend,lock1)

        backend._remoteBatchMove(pairs=pairs,retry=3)






        ## Moves
        if len(moves)>0:
            #---check father dir 
            _dirs = []
            for (src,dst) in moves:
                fd = Path(dst).parent.as_posix()
                if (fd != ".") and (fd not in _dirs):
                    _dirs.append(fd) 
            _dirs = [ d for d in _dirs if d not in backend.metaCache ] 
            _dirs = sorted(_dirs, key=lambda x: x.count('/'), reverse=True)
            nWorkers = self.syncConfig['action_threads'] 
            nWorkers = min(nWorkers,4) 
            _locks = [lock1,lock2,lock3,lock4][:nWorkers]

            with ThreadPoolExecutor(max_workers=nWorkers) as exe:
                for i, d in enumerate(_dirs):
                    log(f"[mkdir]: {d}") 
                    lock = _locks[i % nWorkers]
                    exe.submit(_makedirsIfNeeded,d,backend, lock) 

            backend._remoteBatchMove(moves)



        ## Backups
        if backups:
            # backup_with_copy = config.backup_with_copy
            backup_with_copy = self.syncConfig['backup_with_copy']
            cmd = cmd0.copy()
            if backup_with_copy is None:
                cmd[0] = "copy" if backend._isServersideSupport_copy() else "move"
                debug(f"Automatic Copy Support: {cmd[0]}")
                actionFunc = backend._remoteBatchCopy if backend._isServersideSupport_copy() else backend._remoteBatchMove
            elif backup_with_copy:
                cmd[0] = "copy"
                debug("Always using copy")
                actionFunc = backend._remoteBatchCopy
            else:
                cmd[0] = "move"
                debug("Always using move")
                actionFunc = backend._remoteBatchMove

            pairs = [] 
            for b in backups:
                Src = b 
                Dst = utils.pathjoin( self.backup_path_re[AB], b )
                pairs.append(  ( Src,Dst )  ) 
                _mkdirFilePathAllowed(Dst,backend,lock1)

  
            actionFunc(pairs=pairs,retry=3)



        ## Deletes w/o backup
        if dels_noback:
            backend._remoteBatchDelete( dels )
            # _=input("ok")
        # _=input(f"end of Deletes w/o backup")



    def transfer(self, mode, matched_size, diff_size):
        nActionThreads = self.syncConfig['action_threads']
        # config = self.config
        if mode == "A2B":
            backendSRC, backendDST = self.backend['A'],self.backend['B']
        elif mode == "B2A":
            backendSRC, backendDST = self.backend['B'],self.backend['A']

        if not matched_size and not diff_size:
            return

        # cmd = ["copy"]
        # cmd += ["-v", "--stats-one-line", "--log-format", ""]
        # cmd += ()  # + getattr(config,f'rclone_flags{AB}')
        # ^^^ Doesn't get used here.
        # TODO: Consider using *both* as opposed to just one
        # TODO: Make it more clear in the config

        # We need to be careful about flags for the transfer. Ideally, we would include
        # either --ignore-times or --no-check-dest to *always* transfer. The problem
        # is that if any of them need to retry, it will unconditionally transfer
        # *everything* again!
        #
        # The solution then is to let rclone decide for itself what to transfer from the
        # the file list. The problem here is we need to match `--size-only` for size
        # compare or `--checksum` for hash compare. If the compare is hash, we *already*
        # did it. And even for mtime, we don't want to request the ModTime on remotes
        # like S3. The solution is therefore as follows:
        #   - Decide what changes resulted in size changes (probably most). Run them with
        #     --size-only. Note that if `compare = 'size'`, this is implicit anyway
        #   - For those that should transfer but size has changed, run with nothing or
        #     --checksum. Do not need to consider --size-only since it will have been
        #     captured.
        #
        # This is still imperfect because of the additional rclone calls but it is safer!

        # This flags is not *really* needed but based on the docs (https://rclone.org/docs/#no-traverse),
        # it is likely the case that only a few files will be transfers. This number is a WAG. May change
        # the future or be settable.

        # This was an experiment. Keep it but comment out


        # diff_size first
        if diff_size: # diff_size 是一个 list，内容是文件路径 
            # _ = input (f"mode {mode}: diff_size = {diff_size}")

            # for p in diff_size:
            #     log(f"[copy diff_size]: {p}")
            #     copySingleFileSizeOnly(p,backendSRC, backendDST,self.tmpdir)

            copyManyFile_SizeOnly(diff_size,backendSRC, backendDST,self.tmpdir)




        if matched_size:
            # 文件内容改变但是size没有改变时候触发
            # _ = input (f"mode {mode}:  matched_size = { matched_size }")

            compare = self.syncConfig['compare']
            if compare == "hash":
                with ThreadPoolExecutor(max_workers=int(nActionThreads)) as exe:
                    for p in matched_size:
                        log(f"[copy matched_size,hash]: {p}")
                        exe.submit(copySingleFileHash, p,backendSRC, backendDST,self.tmpdir)
            elif compare == "size":
                raise ValueError("This should NOT HAPPEN")
            else:
                with ThreadPoolExecutor(max_workers=int(nActionThreads)) as exe:
                    for p in matched_size:
                        log(f"[copy matched_size,hash]: {p}")
                        exe.submit(copySingleFileHashNotSupport, p,backendSRC, backendDST,self.tmpdir)




    def copylog(self, remote, srcfile, logname):
        backend = self.backend[remote]
        workDir = self.getWorkDirFromBackend(backend=backend)
        dst = utils.pathjoin(workDir, "logs", logname)
        backend.backend.putFile(localPath=srcfile,rPathRemote=dst) 

    def lock(self, remote, breaklock=False):
        """
        Sets or break the locks. Does *not* check for them first!
        """

        backend = self.backend[remote]
        workDir = self.getWorkDirFromBackend(backend)
        # lockDest = utils.pathjoin(workDir, f"LOCK/LOCK_{self.syncConfig['name']}")
        lockDir = utils.pathjoin(workDir, f"LOCK")
        lockFile = lockDir + "/lockfile" #+ f"{str(int(time.time()))}.lockfile"
        log("")
        if breaklock:
            # log(f"Breaking locks on {remote}. May return errors if {remote} is not locked")
            try:
                backend.backend.deleteFile(lockFile)
            except subprocess.CalledProcessError:
                log("No locks to break. Safely ignore rclone error")
        else:
            log(f"Setting lock on {remote}")
            backend.writeEmptyFile(lockFile)



    def isLocked(self, remote):

        backend = self.backend[remote]

        workDir = self.getWorkDirFromBackend(backend=backend)
        lockDir = utils.pathjoin(workDir, "LOCK")
        # lockDest = utils.pathjoin(workDir, f"LOCK/LOCK_{self.syncConfig['name']}")  
        # lockDest = utils.pathjoin(workDir, f"LOCK/LOCK_GLOBAL")  
        # touch = lockDest in backend.metaCache
        # lockFiles = []
        # for path,info in backend.metaCache.items():
        #     if path.startswith(lockDir) and path != lockDir:
        #         lockFiles.append( os.path.basename(path) ) 
        lockFile = lockDir + "/lockfile"
        # print(lockFile)
        # print(backend.metaCache)
        # _=input('here')
        
        if lockFile in backend.metaCache:
            return True  
        else:
            return False 



    def rmEmptyDirs(self, remote, dirlist):
        """
        !!!Important: This method ONLY remove EMPTY directories. If dir is not empty, DONOT remove it!  
        Remove the directories in dirlist. dirlist is sorted so the deepest
        go first and then they are removed. Note that this is done this way
        since rclone will not delete if *anything* exists there; even files
        we've ignored.
        """

        backendHandler = self.backend[remote]
        findLinkpath = FindSubPath(backendHandler.possibleLinks)
        findDirPath = FindSubPath(backendHandler.possibleDirs)



        # 找到最上层dir    
        rmdirs = util_filter_paths(dirlist)
        # print(f"找到最上层dir={dirlist}")

        if backendHandler._isServersideSupport_Purge():
            for rmd in rmdirs:
                log(f"purge dir:{rmd}")
                backendHandler.backend.purge( rmd ) 
            return 


        # rmdirs = sorted(dirlist, key=lambda x: x.count('/'), reverse=True)
        

        deleteDirs = [] 
        for d in rmdirs: 
            deleteDirs += findDirPath.findSubPath(d) 
        deleteDirs = sorted(deleteDirs, key=lambda x: x.count('/'), reverse=True) 


        # print("dir:",rmdirs)

        deleteLinks = []
        for d in rmdirs:
            deleteLinks += findLinkpath.findSubPath(d)
        # if backendHandler.linkMode == 2:
        #     deleteLinks = [ backendHandler.linkParser. for l in deleteLinks ]

        backendHandler._remoteBatchDelete(deleteLinks)
        

        # print(f"rmdirs (subs)={deleteDirs}")
        # print(f"rmdirs={rmdirs}")
        # print(f"need to delete links={deleteLinks}")


        # _=input(f"rmdirs={rmdirs} @ {AB}")
        def _rmdir(rmdir):
            # _cmd = cmd + [utils.pathjoin(remote, rmdir)]
            try:
                # return rmdir, self.call(_cmd, stream=False, logstderr=False)
                ec = backendHandler.backend.rmdir(rmdir)
                return rmdir, str(ec)
            except subprocess.CalledProcessError:
                # This is likely due to the file not existing. It is acceptable
                # for this error since even if it was something else, not
                # properly removing empty dirs is acceptable
                return rmdir, "<< could not delete >>"

        for rmd in deleteDirs:
            _rmdir(rmd) 
        # print("start delete rmdirs")
        for rmd in rmdirs:
            # print(f"delete-- {rmd}")
            _rmdir(rmd)

    @utils.memoize
    def features(self, remote):
        """Get remote features"""
        # reture dict contains following attr
        # "About": true,
		# "BucketBased": false,
		# "BucketBasedRootOK": false,
		# "CanHaveEmptyDirectories": true,
		# "CaseInsensitive": false,
		# "ChangeNotify": false,
		# "CleanUp": false,
		# "Command": true,
		# "Copy": false,
		# "DirCacheFlush": false,
		# "DirMove": true,
		# "Disconnect": false,
		# "DuplicateFiles": false,
		# "FilterAware": true,
		# "GetTier": false,
		# "IsLocal": true,
		# "ListR": false,
		# "MergeDirs": false,
		# "Move": true,
		# "OpenWriterAt": true,
		# "PublicLink": false,
		# "Purge": true,
		# "PutStream": true,
		# "PutUnchecked": false,
		# "ReadMetadata": true,
		# "ReadMimeType": false,
		# "ServerSideAcrossConfigs": false,
		# "SetTier": false,
		# "SetWrapper": false,
		# "Shutdown": false,
		# "SlowHash": true,
		# "SlowModTime": false,
		# "UnWrap": false,
		# "UserInfo": false,
		# "UserMetadata": true,
		# "WrapFs": false,
		# "WriteMetadata": true,
		# "WriteMimeType": false
        config = self.config
        AB = remote
        remote = getattr(config, f"remote{AB}")
        features = json.loads(
            self.call(
                ["backend", "features", remote],
                stream=False,
            )
        )
        return features.get("Features", {})

    def copy_support(self, remote):
        """
        Return whether or not the remote supports  server-side copy

        Defaults to False for safety
        """
        return self.backend[remote]._isServersideSupport_copy()
    
    def move_support(self, remote):
        """
        Return whether or not the remote supports  server-side move

        Defaults to False for safety
        """
        return self.backend[remote]._isServersideSupport_move()

    def empty_dir_support(self, remote):
        """
        Return whether or not the remote supports empty-dirs

        Defaults to True since if it doesn't support them, calling rmdirs
        will just do nothing
        """
        return True
    
