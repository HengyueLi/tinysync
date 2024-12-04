from .abc import Backend as BaseClass,pathjoin
from .abc import RFC3339_to_unix
import os
import configparser
import logging 
import json 
from pathlib import PurePosixPath
import subprocess



class Backend(BaseClass):

    def __init__(self,dirPath:str,rclone='rclone'):
        super().__init__()
        self._remotePath = dirPath
        self._rclone = rclone
        class Empty:pass 

        self._features = self._getFeature() 
        if self._features.get('Purge',False):
            self.purge = self._purge
        else:
            self.purge = Empty()
            self.purge.__func__ = BaseClass.purge
    
    def _cmd(self,rcmd:str):
        cmd = f"{self._rclone} {rcmd}" 
    
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=True
        )

        stdout = result.stdout
        stderr = result.stderr
        # print("cmd=",cmd) 
        # print("stdout=",stdout)
        # print("sterr=",stderr)
        return stdout,stderr

    
    def _getFeature(self):
        cmd = f"backend features {self._remotePath}"
        stdout,stder = self._cmd(cmd) 
        if "didn't find" in stder:
            print("backend error") 
        features = json.loads(stdout)
        return features['Features']

    def getSyncPath(self)->str:
        """print path position 

        Returns:
            str: _description_
        """      
        if not hasattr(self,"_GETSYNCPATH__"):
            strTag =  f"rclone:"+self._remotePath 
            self._GETSYNCPATH__ = strTag 
        return self._GETSYNCPATH__



    def mkdir(self,rpath:str)->int:
        """ mkdir

        Args:
            rpath (str): path


        Returns:
            int: error code, =0 for success
        """   
        absPath =   pathjoin( self._remotePath,  * rpath.split("/") )
        try:
            rcmd = f"mkdir {absPath}" 
            self._cmd(rcmd=rcmd)
            return 0 
        except Exception as e:
            logging.error(f"error={str(e)}")
            return -1

    def rmdir(self,rPathRemote:str)->int: 
        """remove a empty dir. 
        if possible, try to concrete method [purge]

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """
        absPath =   pathjoin( self._remotePath,  * rPathRemote.split("/") )
        rcmd = f"rmdir {absPath}"
        stdout = self._cmd(rcmd)
        return 0 



    def listPath(self,rpath:str,mtime:bool=True,hash:bool=False)->list[ dict ]:
        """list files and directories (not recursively) in a given (relative) path. 

        Args:
            rpath (_type_): relative path , example: rpath = "this/path", rpath = "" 
            mtime (bool, optional): contains mtime or not.
            hash (bool, optional): contains hash value or not.

        Returns:
            list[ dict ]: dict = { "name":str,  "Size":int, "mtime":float, 'type':'d'/'f'/'l',  'target':str (for link case), 'ltype':'d'/'f' (for link case)  }
            if hash, dict should contain dict["Hashes"] = {...}, for example dict["Hashes"]={"sha1":"66c..."}
        """    
        opt = ' --no-mimetype '    
        if hash:
            opt += " --hash "  
        absPath =   pathjoin( self._remotePath,  * rpath.split("/") )
        absPath = PurePosixPath(absPath).as_posix()
        stdout,sterr = self._cmd(f"lsjson {opt} {absPath}")
        items = json.loads(stdout)
        res = [] 
        for item in items:
            mtime = item.get('ModTime',None) 
            mtime = int(RFC3339_to_unix(mtime)) if mtime else None
            res.append({
                "name":item['Name'], 
                'Size':item['Size'], 
                'mtime':mtime, 
                'type':'d' if item['IsDir'] else 'f',
            })
        return res 


    def putFile(self,localPath:str,rPathRemote:str): 
        """a local file <localPath> is uploading to remote at be <rPathRemote>

        Args:
            localPath (str): abs-path of a local path
            rPathRemote (str): relative path of a remote place 
        """     
        absPath =   PurePosixPath(pathjoin( self._remotePath,  * rPathRemote.split("/") )).as_posix()       
        cmd = f'copyto {localPath} {absPath}'
        self._cmd(cmd) 

    def getFile(self,rPathRemote:str,localPath:str)->int: 
        """download a remote file <rPathRemote> to be local file <localPath> 

        Args:
            rPathRemote (str): relative path of a remote place 
            localPath (str): abs-path of a local path
        Returns:
            int: 0 -> success,  -1 -> file not exist
        """  
        absPath =   PurePosixPath(pathjoin( self._remotePath,  * rPathRemote.split("/") )).as_posix()   
        cmd = f'copyto {absPath} {localPath}'
        stdout,stderr=self._cmd(cmd) 
        if 'not found' in stderr:
            return -1
        else:
            return 0


    def remoteMove(self,rPathSrc:str,rPathDst:str)->int:
        """ OPTIONAL IMPLEMENTATION 
        move a source (of file or dir) to target path

        Args:
            rPathSrc (str): source path
            rPathDst (str): target path 

        Returns:
            int: error code, 0
        """        
        src = PurePosixPath(pathjoin( self._remotePath,  * rPathSrc.split("/") )).as_posix()
        dst = PurePosixPath(pathjoin( self._remotePath,  * rPathDst.split("/") )).as_posix()
        cmd = f"moveto {src} {dst}"
        self._cmd(cmd) 
        return 0  
    
    def deleteFile(self,rPathRemote:str): 
        """delete a remote file <rPathRemote>

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        src = PurePosixPath(pathjoin( self._remotePath,  * rPathRemote.split("/") )).as_posix()
        cmd = f"delete {src}" 
        stdo,stde = self._cmd(cmd)  
        if 'not found' in stde:
            return -1 
        else:
            return 0 



    def _purge(self,rPathRemote:str)->int: 
        """ OPTIONAL IMPLEMENTATION 
        remove a dir and all of its inside files and dirs.

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success
        """ 
        src = PurePosixPath(pathjoin( self._remotePath,  * rPathRemote.split("/") )).as_posix()
        cmd = f"purge {src}"
        self._cmd(cmd)
        return 0 
