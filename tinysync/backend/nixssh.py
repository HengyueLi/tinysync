from .abc import Backend as BaseClass
import paramiko
import datetime  
import os 
import stat
import logging 
from .abc import pathjoin



class Backend(BaseClass):

    def __init__(self,dirPath:str,paramikoConfig:dict):
        super().__init__()
        self._dirPath = dirPath
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.paramikoConfig = paramikoConfig
        try:
            self.client.connect(**paramikoConfig) 
        except:
            raise Exception("Failed to connect")
        self.sftp = self.client.open_sftp()
        self.ssh_session = self.client.get_transport().open_session()

    def __del__(self):
        self.sftp.close()
        self.client.close()
        self.ssh_session.close()

    def getSyncPath(self)->str:
        """print path position 

        Returns:
            str: _description_
        """        
        return f"nixssh:{self.paramikoConfig['hostname']}"+self._dirPath

    def _getValidPath(self,rpath):
        return pathjoin( self._dirPath, rpath )



    def mkdir(self,rpath:str)->int:
        """ mkdir

        Args:
            rpath (str): path


        Returns:
            int: error code, =0 for success
        """   
        p = self._getValidPath(rpath)
        try:
            self.sftp.mkdir(p) 
            return 0 
        except IOError as e:
            logging.error(f"Failed to create directory {rpath}: {e}")

    def rmdir(self,rPathRemote:str)->int: 
        """remove a empty dir. 
        if possible, try to concrete method [purge]

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """
        p = self._getValidPath(rPathRemote) 
        try:
            self.sftp.rmdir(p)
            return 0 
        except IOError as e:
            # Check if the error is because the directory does not exist
            if e.errno == 2:  # errno 2 means 'No such file or directory'
                logging.error(f"Directory does not exist: {rPathRemote}")
                return -1 
            else:
                logging.error(f"Failed to remove directory {rPathRemote}: {e}")
                return -2 
         


    def listPath(self,rpath:str,mtime:bool=True,hash:bool=False)->list[ dict ]:
        """list files and directories (not recursively) in a given (relative) path. 

        Args:
            rpath (_type_): relative path , example: rpath = "this/path", rpath = "" 
            mtime (bool, optional): contains mtime or not.
            hash (bool, optional): contains hash value or not.

        Returns:
            list[ dict ]: dict = { "Size":int, "mtime":float, 'type':'d'/'f'/'l',  'target':str (for link case), 'ltype':'d'/'f' (for link case)  }
            if hash, dict should contain dict["Hashes"] = {...}, for example dict["Hashes"]={"sha1":"66c..."}
        """         
        meta = []
        apath = self._getValidPath(rpath)
        files = self.sftp.listdir_attr(apath)
        for file_attr in files:
            name = file_attr.filename
            abspath = apath + "/" + name
            info = {}
            mode = file_attr.st_mode
            info['mtime'] = int( datetime.datetime.fromtimestamp(file_attr.st_mtime).timestamp() )
            info['name'] = name
            info['Size'] = file_attr.st_size
            if stat.S_ISDIR(mode):
                info['type'] = 'd' 
            elif stat.S_ISREG(mode):
                info['type'] = 'f' 
            elif stat.S_ISLNK(mode):
                info['type'] = 'l'
                info['target'] = self.sftp.readlink(abspath)
                info['ltype'] = self.symlinktype
                try:
                    lpath = self.sftp.normalize(abspath)
                    target_attr = self.sftp.stat(lpath)
                    if stat.S_ISREG(target_attr.st_mode):
                        info['ltype'] = 'f' 
                        lpath = self.sftp.normalize(abspath)
                except FileNotFoundError:
                    pass 
            meta.append(info) 
        return meta 
            

    def putFile(self,localPath:str,rPathRemote:str): 
        """a local file <localPath> is uploading to remote at be <rPathRemote>

        Args:
            localPath (str): abs-path of a local path
            rPathRemote (str): relative path of a remote place 
        """     
        rPathAbs = self._getValidPath( rPathRemote )
        self.sftp.put(localPath, rPathAbs)
        local_mtime = os.path.getmtime(localPath)
        self.sftp.utime(rPathAbs, (local_mtime, local_mtime))


    def getFile(self,rPathRemote:str,localPath:str)->int: 
        """download a remote file <rPathRemote> to be local file <localPath> 

        Args:
            rPathRemote (str): relative path of a remote place 
            localPath (str): abs-path of a local path
        Returns:
            int: 0 -> success,  -1 -> file not exist
        """ 
        rPathAbs = self._getValidPath( rPathRemote )

        try:
            self.sftp.get(rPathAbs, localPath)
            remote_file_attrs = self.sftp.stat(rPathAbs)
            remote_mtime = remote_file_attrs.st_mtime
            os.utime(localPath, (remote_mtime, remote_mtime))
            return 0 
        except IOError as e:
            if e.errno == 2:
                logging.error(f"Error: The remote file does not exist: {rPathRemote}")
                return -1 
            else:
                logging.error(f"IOError: {e}")
                return -99
        except FileNotFoundError:
            logging.error(f"FileNotFoundError: The file {rPathRemote} does not exist on the remote server.")
            return -1 


    def deleteFile(self,rPathRemote:str): 
        """delete a remote file <rPathRemote>

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        p = self._getValidPath(rPathRemote) 
        try:
            self.sftp.remove(p)
            return 0
        except FileNotFoundError:
            logging.error(f"File not found: {rPathRemote} could not be deleted because it does not exist.")
            return -1
        except IOError as e:
            logging.error(f"Error: Could not delete file {rPathRemote}. {e}")
            return -99 
        

    def remoteMove(self,rPathSrc:str,rPathDst:str)->int:
        """ OPTIONAL IMPLEMENTATION 
        move a source (of file or dir) to target path

        Args:
            rPathSrc (str): source path
            rPathDst (str): target path 

        Returns:
            int: error code, 0
        """        
        src = self._getValidPath(  rPathSrc ) 
        dst = self._getValidPath(  rPathDst )
        try:
            self.sftp.rename(src, dst) 
            return 0
        except:
            return -1   
    
    def remoteCopy(self,rPathSrc:str,rPathDst:str)->int:
        """ OPTIONAL IMPLEMENTATION 
        copy a source (of file or dir) to target path

        Args:
            rPathSrc (str): source path
            rPathDst (str): target path 

        Returns:
            int: error code, 0
        """        
        src = self._getValidPath(  rPathSrc ) 
        dst = self._getValidPath(  rPathDst )
        cp_command = f"cp {src} {dst}"
        self.ssh_session.exec_command(cp_command) 
        exit_status = self.ssh_session.recv_exit_status()  # Blocking call
        if exit_status == 0:
            return 0 
        else:
            return -1


    def mklink(self,rPathRemote:str,target:str,isDir:bool):
        """ OPTIONAL IMPLEMENTATION 
        create a symbolic link 

        Args:
            rPathRemote (str): file path
            target (str): target of the link
            isDir (bool): is dir or not 
        """        
        p = self._getValidPath(rPathRemote)
        self.sftp.symlink(target,p)

    def purge(self, rPathRemote: str) -> int:
        dst = self._getValidPath(  rPathRemote ) 
        cp_command = f"rm -rf {dst}"
        self.ssh_session.exec_command(cp_command) 
        exit_status = self.ssh_session.recv_exit_status()  # Blocking call
        if exit_status == 0:
            return 0 
        else:
            return -1