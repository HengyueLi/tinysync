import copy
import json
import time
import sys, os, shutil
import warnings
import ctypes

from .log import debug, log
from . import utils
from .backendMethods import Rclone as API,BackendHandler 
# from .rclone import Rclone_bk as API 
from .dicttable import DictTable
from .backend.abc import Backend
import logging
import hashlib
from pathlib import PurePosixPath
import tempfile
from .config import configuration






#  DOTO:    filename.startswith(".syncrclone"):  






def sc(s,l=10):
    r = s.ljust(l," ") 
    if len(r)>l:
        r = "..." + r[-(l-3):]
    return r 






def find_empty_folders_from_files(pre, new):
    # 提取所有文件的所有可能的父目录
    all_folders = set()
    non_empty_folders = set()

    # 处理Pre集合，建立所有可能的父目录集合
    for file_path in pre:
        path_parts = list(PurePosixPath(file_path).parents)
        path_parts.reverse()  # 从最近的父目录到根目录
        for folder in path_parts:
            all_folders.add(str(folder))

    # 处理New集合，更新非空的父目录集合
    for file_path in new:
        path_parts = list(PurePosixPath(file_path).parents)
        path_parts.reverse()  # 从最近的父目录到根目录
        for folder in path_parts:
            non_empty_folders.add(str(folder))

    # 所有在all_folders中但不在non_empty_folders中的目录即为空
    empty_folders = all_folders - non_empty_folders

    empty_folders.discard(".")
    return empty_folders




def isWinAdmin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        return False

class Sync:

    def configValidation(self,syncConfig,handlers:dict[str,BackendHandler]):
            
        # 检查追踪move是否有效，如果backend不支持move，自动修改
        for AB in handlers:
            if handlers[AB].backend.renames:
                if not handlers[AB]._isServersideSupport_move():
                    handlers[AB].backend.renames = False 
                    log(f"backend [{AB}] not support move, <rename> makes no sense, ignored")

        
    @staticmethod
    def resetconfig_Windows_linkMode2(syncConfig:dict):
        # 在windows上创建软连接需要admin
        if syncConfig['linkMode'] == 2 and sys.platform == 'win32':
            if not isWinAdmin():
                syncConfig['linkMode'] = 0 
                log("!!! Windows with linkMode=2 needs Admin permission. Reset to linkMode=0 (ignore symbolic links) ") 
        return syncConfig









    def __init__(self,workdir,syncConfig, backendA:Backend, backendB:Backend, break_lock=None,returnVal=[None],**kw):
        """_summary_

        Args:
            workdir (_type_): _description_
            syncConfig (_type_): _description_
            backendA (Backend): _description_
            backendB (Backend): _description_
            break_lock (_type_, optional): _description_. Defaults to None.
            returnVal (list, optional): _description_. Defaults to [None].
            saveStateB (bool, optional): if True, current state on B will NOT listed in realtime but read from a previously stored data. This is for realtime-sync tool 
        """        

        syncConfig = self.resetconfig_Windows_linkMode2(syncConfig)

        backendHandlerA = BackendHandler(backendA,workdir,syncConfig=syncConfig)
        backendHandlerB = BackendHandler(backendB,workdir,syncConfig=syncConfig)
        self.backend = {'A':backendHandlerA,'B':backendHandlerB}

        pathA = backendA.getSyncPath()
        pathB = backendB.getSyncPath()
        syncConfig['name'] = hashlib.md5(f"{pathA}_{pathB}".encode()).hexdigest()[:5]

        self.configValidation(syncConfig,self.backend)

        self.t0 = time.time()
        self.shell_time = 0.0
        self.now = time.strftime("%Y-%m-%dT%H%M%S", time.localtime())
        self.now_compact = self.now.replace("-", "")

        self.syncConfig = syncConfig

        self.confbackup = self.syncConfig['backup']

        self.logname = f"{self.syncConfig['name']}_{self.now}.log"
        
        self.api = API( backendA=backendHandlerA, backendB=backendHandlerB,nowStrTag=self.now,syncConfig=self.syncConfig,workdir=workdir)

        # self.run_shell(pre=True)

        if break_lock:
            self.api.lock(breaklock=True, remote=break_lock)
            return 

        # Get file lists
        log("")
        log("Refreshing file lists concurrently")
        self.api.clean_link_store()



        listA = utils.ReturnThread(target=self.api.file_list, kwargs=dict(remote="A")).start()
        time.sleep(2e-6)  # 2 microseconds just to make sure the time_ns() changes
        listB = utils.ReturnThread(target=self.api.file_list, kwargs=dict(remote="B")).start()

        self.currA, self.prevA = listA.join()
        self.currB, self.prevB = listB.join()

        # print("here") 
        # print(backendHandlerA.metaCache,888)
        # raise 

        self.api.checkAndMakeRemoteWorkdir()
 

        log(f"Refreshed file list on A ")
        log(utils.file_summary(self.currA))

        log(f"Refreshed file list on B ")
        log(utils.file_summary(self.currB))

        if self.syncConfig['set_lock']:
             
            if self.api.isLocked('A'):
                returnVal[0] = -1 # A locked
                return 
            if self.api.isLocked('B') :
                returnVal[0] = -2 # B locked
                return
            self.api.lock(remote='A')
            self.api.lock(remote='B')

        # Store the original "curr" list as the prev list for speeding
        # up the hashes. Also used to tag checking.
        # This makes a copy but keeps the items
        self.currA0 = self.currA.copy()
        self.currB0 = self.currB.copy()


        self.remove_common_files() # self.currA, self.prevA, self.currB, self.prevB 都得到了更新，除去了  currA ^ currB

        if len(self.currA)>0:
            log("==================[delta on A]:")
            for f in self.currA:
                hashStr = str(f.get('Hashes',{}))
                log(f" {sc(f['Path'],30)}  {sc(str(f['mtime']),11)}  {sc(str(f['Size']),18)} {hashStr}")         

        if len(self.currB)>0:
            log("==================[delta on B]:")
            for f in self.currA:
                hashStr = str(f.get('Hashes',{}))
                log(f" {sc(f['Path'],30)}  {sc(str(f['mtime']),11)}  {sc(str(f['Size']),18)} {hashStr}")   
        
        self.process_non_common()  # builds new,del,tag,backup,trans,move lists

        self.echo_queues("Initial")

        # Track moves from new and del lists. Adds to moves list()
        self.track_moves("A")
        self.track_moves("B")

        self.echo_queues("After tracking moves")

        # Apply moves and transfers from the new and tag lists.
        # After this, we only care about del, backup, and move lists
        self.process_new_tags("A")
        self.process_new_tags("B")

        self.echo_queues("After processing new and tags")

        
        # summarize also sets the syncstats dict used by stats() below
        self.summarize(dry=False)


        ## Perform deletes, backups, and moves

        # Do actions. Clear the backup list if not using rather than keep around.
        # This way, we do not accidentally transfer it if not backed up
        if not self.confbackup:  # Delete in place though I don't think it matters
            del self.backupA[:]
            del self.backupB[:]
        log("")
        log("Performing Actions on A")

        self.api.delete_backup_move("A", self.delA, self.backupA, self.movesA)
        if self.confbackup and (self.delA or self.backupA):
            log(f"""Backups for A stored in '{self.api.backup_path["A"]}'""")


        log("")
        log("Performing Actions on B")
        self.api.delete_backup_move("B", self.delB, self.backupB, self.movesB)
        if self.confbackup and (self.delB or self.backupB):
            log(f"""Backups for B stored in '{self.api.backup_path["B"]}'""")


        # Perform final transfers
        self.sumA = utils.file_summary(
            [self.currA.query_one(Path=f) for f in self.transA2B]
        )
        log("")
        log(f"A >>> B {self.sumA}")

        self.api.transfer("A2B", *self.split_transfer_lists_matching_size("A2B"))

        self.split_transfer_lists_matching_size("B2A")
        self.sumB = utils.file_summary(
            [self.currB.query_one(Path=f) for f in self.transB2A]
        )
        log("")
        log(f"A <<< B {self.sumB}")
        self.api.transfer("B2A", *self.split_transfer_lists_matching_size("B2A"))

        # Update lists if needed
        log("")

        log("Apply changes to file lists instead of refreshing")
        new_listA, new_listB = self.avoid_relist()

        if self.api.empty_dir_support("A"):
            emptyA = find_empty_folders_from_files(pre={f["Path"] for f in self.currA0},new={f["Path"] for f in new_listA})
            emptyA = [d for d in emptyA if d not in self.backend['B'].metaCache]
            self.api.rmEmptyDirs("A", emptyA)

        if self.api.empty_dir_support("B"):
            emptyB = find_empty_folders_from_files(pre={f["Path"] for f in self.currB0},new={f["Path"] for f in new_listB})
            emptyB = [d for d in emptyB if d not in self.backend['A'].metaCache]
            self.api.rmEmptyDirs("B", emptyB)


        self.new_listA, self.new_listB = new_listA, new_listB

        log("Uploading filelists")
        self.api.push_file_list(new_listA, remote="A")
        self.api.push_file_list(new_listB, remote="B")

        self.deleteExpiredData()

        self.stats()

        # self.run_shell(pre=False)
        for line in self.stats().split("\n"):
            log(line)
        self.dump_logs()

        # remove lock
        if self.syncConfig['set_lock']:
            self.api.lock(remote='A',breaklock=True)
            self.api.lock(remote='B',breaklock=True)


    def deleteExpiredData(self):
        now = time.time() 
        expiredTS = now - float(self.syncConfig['logExpiredDays'])*24*3600
        for AB in ('A','B'):
            self.api.cleanWorkData(AB=AB,timestamp=expiredTS) 


    # def purge

    def dump_logs(self):
        if not self.syncConfig['local_log_dest'] and not self.syncConfig['save_logs']:
            log("Logs are not being saved")
            return

        logname = self.logname

        log("where A and B are:")
        log(f"A: {self.backend['A'].backend.getSyncPath()}")
        log(f"B: {self.backend['B'].backend.getSyncPath()}")

        # log these before dumping
        if self.syncConfig['local_log_dest']:
            log(
                f"Logs will be saved locally to '{os.path.join(self.syncConfig['local_log_dest'],logname)}'"
            )
        if self.syncConfig['save_logs']:
            log(f"Logs will be saved on workdirs to {logname}")

        tfile = os.path.join(self.api.tmpdir, "log")
        log.dump(tfile)


        if self.syncConfig['local_log_dest']:
            dest = os.path.join(self.syncConfig['local_log_dest'], logname)
            try:
                os.makedirs(os.path.dirname(dest))
            except OSError:
                pass
            shutil.copy2(tfile, dest)

        if self.syncConfig['save_logs']:
            self.api.copylog("A", tfile, logname)
            self.api.copylog("B", tfile, logname)


    def summarize(self, dry=False):
        """
        dry can be True, False, or None where None is to show the planned
        """
        self.syncstats = syncstats = {}
        if dry is True:
            tt = "(DRY RUN) "
            log(tt.strip())
        elif dry is False:
            tt = ""
        elif dry is None:
            tt = "(PLANNED) "
        else:
            raise ValueError()  # Just in case I screw up later

        attr_names = {
            "del": "Delete (with{} backup)".format(
                "out" if not self.confbackup else ""
            ),
            "backup": "Backup",
            "new": "New",
        }
        if not self.confbackup:
            attr_names["backup"] = "Will overwrite (w/o backup)"

        for AB in "AB":
            log("")
            log(f"Actions queued on {AB}:")
            for attr in ["del", "backup", "moves", "new"]:
                files = getattr(self, f"{attr}{AB}")
                syncstats[f"{attr}{AB}"] = len(files)
                for file in files:
                    if attr == "moves":
                        log(f"{tt}Move on {AB}: '{file[0]}' --> '{file[1]}'")
                    else:
                        log(f"{tt}{attr_names.get(attr,attr)} on {AB}: '{file}'")

        if dry is False:
            return

        sumA = utils.file_summary([self.currA.query_one(Path=f) for f in self.transA2B])
        sumB = utils.file_summary([self.currB.query_one(Path=f) for f in self.transB2A])

        log("")
        log(f"{tt}A >>> B {sumA}")
        for file in self.transA2B:
            log(f"{tt}Transfer A >>> B: '{file}'")
        log("")
        log(f"{tt}A <<< B {sumB}")
        for file in self.transB2A:
            log(f"{tt}Transfer A <<< B: '{file}'")


    def echo_queues(self, descr=""):
        debug(f"Printing Queueus {descr}")
        for attr in ["new", "del", "tag", "backup", "trans", "moves"]:
            for AB in "AB":
                BA = "B" if AB == "A" else "A"
                if attr == "trans":
                    pa = f"{attr}{AB}2{BA}"
                else:
                    pa = f"{attr}{AB}"
                debug("   ", pa, getattr(self, pa))

    def remove_common_files(self):
        """
        Removes files common in the curr list from the curr lists and,
        if present, the prev lists
        """
        # config = self._conf
        commonPaths = set(file["Path"] for file in self.currA)
        commonPaths.intersection_update(file["Path"] for file in self.currB)

        delpaths = set()
        for path in commonPaths:
            q = {"Path": path}
            # We KNOW they exists for both
            fileA, fileB = self.currA[q], self.currB[q]
            if not self.compare(fileA, fileB):
                continue
            delpaths.add(path)

        for attr in ["currA", "prevA", "currB", "prevB"]:
            new = DictTable(
                [f for f in getattr(self, attr) if f["Path"] not in delpaths],
                fixed_attributes=["Path", "Size", "mtime"],
            )
            setattr(self, attr, new)

        debug(
            f"Found {len(commonPaths)} common paths with {len(delpaths)} matching files"
        )
        

    def process_non_common(self):
        """
        Create action lists (some need more processing) and then populate
        with all remaining files
        次函数之前调用了remove_common_files, self.currA, self.prevA, self.currB, self.prevB 都得到了更新，除去了  currA ^ currB
        """
        # config = self._conf

        # These are for classifying only. They are *later* translated
        # into actions
        self.newA, self.newB = list(), list()  # Will be moved to transfer
        self.delA, self.delB = (
            list(),
            list(),
        )  # Action but may be modified by move tracking later
        self.tagA, self.tagB = list(), list()  # Will be tagged (moved) then transfer

        # These will not need be modified further.
        # -------- LEGACY note
        # self.backup{A/B} are actually not needed but because backups are now handled
        # by --backup-dir and rclone. But, I keep them around since they may be useful
        # for diagnostics. Whenever they are added, a "# Legacy -- see note"
        self.backupA, self.backupB = list(), list()
        self.transA2B, self.transB2A = list(), list()
        self.movesA, self.movesB = (
            list(),
            list(),
        )  # Not used here but created for use elsewhere

        # All paths. Note that common paths with equal files have been cut
        allPaths = set(file["Path"] for file in self.currA)
        allPaths.update(file["Path"] for file in self.currB)

        # NOTE: Final actions will be done in the following order
        # * Delete
        # * Backup -- Always assign but don't perform if --no-backup
        # * Move (including tag)
        # * Transfer
        log("")
        for path in allPaths:
            # print(f"[[[for path in alpha+beta]]]>>>>,path={path}")
            fileA = self.currA[{"Path": path}]
            fileB = self.currB[{"Path": path}]
            fileBp = self.prevB[{"Path": path}]
            fileAp = self.prevA[{"Path": path}]

            # print("process:=====") 
            # print(f"currentA=:",fileA)  
            # print(f"currentB=:",fileB)  
            # print(f"preA=:",fileAp) 
            # print(f"preB=:",fileBp) 

            if fileA is None:  # fileB *must* exist
                if not fileBp:
                    debug(f"File '{path}' is new on B")
                    self.newB.append(path)  # B is new
                elif self.compare(fileB, fileBp):
                    debug(f"File '{path}' deleted on A")
                    self.delB.append(path)  # B must have been deleted on A
                else:
                    log(
                        f"DELETE CONFLICT: File '{path}' deleted on A but modified on B. Transfering"
                    )
                    self.transB2A.append(path)
                continue

            if fileB is None:  # fileA *must* exist
                if not fileAp:
                    debug(f"File '{path}' is new on A")
                    self.newA.append(path)  # A is new
                elif self.compare(fileA, fileAp):
                    debug(f"File '{path}' deleted on A")
                    self.delA.append(path)  # A must have been deleted on B
                else:
                    log(
                        f"DELETE CONFLICT: File '{path}' deleted on B but modified on A. Transfering"
                    )
                    self.transA2B.append(path)
                continue

            # We *know* they do not agree since this common ones were removed.
            # Now must decide if this is a conflict or just one was modified
            compA = self.compare(fileA, fileAp)
            compB = self.compare(fileB, fileBp)

            debug(
                f"Resolving:\n{json.dumps({'A':fileA,'Ap':fileAp,'B':fileB,'Bp':fileB},indent=1)}"
            )

            if compA and compB:
                # This really shouldn't happen but if it does, just move on to
                # conflict resolution
                debug(
                    f"'{path}': Both A and B compare to prev but do not agree. This is unexpected."
                )
            elif not compA and not compB:
                # Do nothing but note it. Deal with conflict below
                debug(f"'{path}': Neither compare. Both modified or both new")
            elif compA and not compB:  # B is modified, A is not
                debug(f"'{path}': Modified on B only")
                self.transB2A.append(path)
                self.backupA.append(path)
                continue
            elif not compA and compB:  # A is modified, B is not
                debug(f"'{path}': Modified on A only")
                self.transA2B.append(path)
                self.backupB.append(path)
                continue

            # They conflict! Handle it.
            mA, mB = fileA.get("mtime", None), fileB.get("mtime", None)
            sA, sB = fileA["Size"], fileB["Size"]

            txtA = utils.unix2iso(mA) if mA else "<< not found >>"
            txtA += f" ({sA:d} bytes)"
            txtB = utils.unix2iso(mB) if mB else "<< not found >>"
            txtB += f" ({sB:d} bytes)"

            if self.syncConfig['conflict_mode'] not in {"newer", "older", "newer_tag"}:
                mA, mB = sA, sB  # Reset m(AB) to s(AB)

            if (
                not mA or not mB
            ):  # Either never set for non-mtime compare or no mtime listed
                warnings.warn("No mtime found. Resorting to size")
                mA, mB = sA, sB  # Reset m(AB) to s(AB)

            log(f"CONFLICT '{path}'")
            log(f"    A: {txtA}")
            log(f"    B: {txtB}")

            txt = f"    Resolving with mode '{self.syncConfig['conflict_mode']}'"

            if self.syncConfig['tag_conflict']:
                tag_or_backupA = self.tagA
                tag_or_backupB = self.tagB
                txt += " (tagging other)"
            else:
                tag_or_backupA = self.backupA
                tag_or_backupB = self.backupB

            if self.syncConfig['conflict_mode'] == "A":
                self.transA2B.append(path)
                tag_or_backupB.append(path)
            elif self.syncConfig['conflict_mode'] == "B":
                self.transB2A.append(path)
                tag_or_backupA.append(path)
            elif self.syncConfig['conflict_mode'] == "tag":
                self.tagA.append(path)  # Tags will *later* be added to transfer queue
                self.tagB.append(path)
            elif not mA or not mB or mA == mB:
                txt = f"    Cannot resolve conflict with '{self.syncConfig['conflict_mode']}'. Reverting to tagging both"
                self.tagA.append(path)  # Tags will *later* be added to transfer queue
                self.tagB.append(path)
            elif mA > mB:
                if self.syncConfig['conflict_mode'] in ("newer", "larger"):
                    self.transA2B.append(path)
                    tag_or_backupB.append(path)
                    txt += "(keep A)"
                elif self.syncConfig['conflict_mode'] in ("older", "smaller"):
                    self.transB2A.append(path)
                    tag_or_backupA.append(path)
                    txt += "(keep B)"
            elif mA < mB:
                if self.syncConfig['conflict_mode'] in ("newer", "larger"):
                    self.transB2A.append(path)
                    tag_or_backupA.append(path)
                    txt += "(keep B)"
                elif self.syncConfig['conflict_mode'] in ("older", "smaller"):
                    self.transA2B.append(path)
                    tag_or_backupB.append(path)
                    txt += "(keep A)"
            else:  # else: won't happen since we validated conflict modes
                raise ValueError(
                    "Comparison Failed. Please report to developer"
                )  # Should not be here

            log(txt)

    def track_moves(self, remote):
        # config = self._conf
        # print(f"---remote={remote}")
        backend = self.backend[remote].backend
        renames = backend.renames
        
        AB = remote
        BA = list(set("AB") - set(AB))[0]
        # remote = {"A": config.remoteA, "B": config.remoteB}[remote]

        rename_attrib = renames
        if not rename_attrib:
            return
        
        # A file move is *only* tracked if it marked
        # (1) Marked as new
        # (2) Can be matched via renames(A/B) to a remaining file in prev
        # (3) The same file is marked for deletion (No need to check anything
        #     since a file is *only* deleted if it was present in the last sync
        #     and unmodified. So it is safe to move it

        new = getattr(self, f"new{AB}")  # on remote -- list

        curr = getattr(self, f"curr{AB}")  # on remote -- DictTable
        prev = getattr(self, f"prev{AB}")  # on remote -- DictTable

        if not new or not curr or not prev:
            debug("No need to move track")
            return
        
        delOther = getattr(self, f"del{BA}")  # On OTHER side -- list
        moveOther = getattr(self, f"moves{BA}")  # on OTHER side - list

        # ALWAYS query size. This will cut out a lot of potential matches which
        # is good since hash and mtime need to search. (We search on hash in case the
        # do not always share a common one)
        for path in new[:]:  # (1) Marked as new. Make sure to iterate a copy
            debug(f"Looking for moves on {AB}: '{path}'")
            currfile = curr[{"Path": path}]

            prevfiles = list(prev.query({"Size": currfile["Size"]}))

            # The mtime and hash comparisons are in loops but this is not too bad
            # since the size check *greatly* reduces the size of the loops

            # Compare time with tol.
            if rename_attrib in ["mtime"]:
                prevfiles = [
                    f
                    for f in prevfiles
                    if abs(f["mtime"] - currfile["mtime"]) < self.syncConfig['dt']
                ]

            # Compare hashes one-by-one in case they're not all the same types
            if rename_attrib == "hash":
                _prevfiles = []
                for prevfile in prevfiles:
                    hcurr = currfile.get("Hashes", {})
                    hprev = prevfile.get("Hashes", {})

                    # Just because there are common hashes, does *not* mean they are
                    # all populated. e.g, it could be a blank string.
                    # It is also possible for there to not be common hashes if the lists
                    # were not refreshed
                    common = {k for k, v in hcurr.items() if v.strip()}.intersection(
                        k for k, v in hprev.items() if v.strip()
                    )
                    if common and all(hcurr[k] == hprev[k] for k in common):
                        _prevfiles.append(prevfile)
                prevfiles = _prevfiles  # rename with the new lists

            if not prevfiles:
                debug(f"No matches for '{path}' on {AB}")
                continue

            if len(prevfiles) > 1:
                log(f"Too many possible previous files for '{path}' on {AB}")
                for f in prevfiles:
                    log(f"   '{f['Path']}'")
                continue
            prevpath = prevfiles[0]["Path"]  # (2) Previous file

            if prevpath not in delOther:
                debug(f"File '{path}' moved from '{prevpath}' on {AB} but modified")
                continue
            
            # Move it instead
            new.remove(path)
            delOther.remove(prevpath)
            moveOther.append((prevpath, path))
            debug(f"Move found: on {BA}: '{prevpath}' --> '{path}'")

    def process_new_tags(self, remote):
        """Process new into transfers and tags into moves"""
        # config = self._conf
        AB = remote
        BA = list(set("AB") - set(AB))[0]
        # remote = {"A": config.remoteA, "B": config.remoteB}[remote]

        new = getattr(self, f"new{AB}")
        tag = getattr(self, f"tag{AB}")
        trans = getattr(self, f"trans{AB}2{BA}")
        moves = getattr(self, f"moves{AB}")

        for file in tag:
            root, ext = os.path.splitext(file)
            dest = f"{root}.{self.now_compact}.{AB}{ext}"
            moves.append((file, dest))
            debug(f"Added '{file}' --> '{dest}'")

            trans.append(dest)  # moves happen before transfers!

        trans.extend(new)

    def compare(self, file1, file2):
        """Compare file1 and file2 (may be A or B or curr and prev)"""
        # config = self._conf
        compare = (
            self.syncConfig['compare']
        )  # Make a copy as it may get reset (str is immutable so no need to copy)
        # print("@compare") 
        # print(f"param[compare]=",compare) 
        # print("file1=",file1)
        # print("file2=",file2)
        if not file1:
            return False
        if not file2:
            return False

        if compare == "hash":
            h1 = file1.get("Hashes", {})
            h2 = file2.get("Hashes", {})
            # Just because there are common hashes, does *not* mean they are
            # all populated. e.g, it could be a blank string.
            # It is also possible for there to not be common hashes if the lists
            # were not refreshed
            common = {k for k, v in h1.items() if v.strip()}.intersection( k for k, v in h2.items() if v.strip() )
            if common:
                return all(h1[k] == h2[k] for k in common)
            else:
                msg = "No common hashes found and/or one or both remotes do not provide hashes"
                if self.syncConfig['hash_fail_fallback']:
                    msg += f". Falling back to '{self.syncConfig['hash_fail_fallback']}'"
                    warnings.warn(msg)
                    compare = self.syncConfig['hash_fail_fallback']
                else:
                    compare = 'size'
                    msg += f". <hash_fail_fallback> not set, compare by 'size'"


        # Check size either way
        if file1["Size"] != file2["Size"]:
            return False

        if compare == "size":  # No need to compare mtime
            return True

        if "mtime" not in file1 or "mtime" not in file2:
            warnings.warn(f"File do not have mtime. Using only size")
            return True  # Only got here size is equal

        return abs(file1["mtime"] - file2["mtime"]) <= self.syncConfig['dt']
    
    # def compare_hash(self,file1,file2):



    def split_transfer_lists_matching_size(self, mode):
        """
        Split transfers into whether they match size or not. See documentation
        of rclone.transfer for explanation
        """
        if mode == "A2B":
            trans = self.transA2B
            src = self.currA
            dst = self.currB
        elif mode == "B2A":
            trans = self.transB2A
            src = self.currB
            dst = self.currA
        else:
            raise ValueError("bad mode")

        matched_size = []
        diff_size = []

        for file in trans:
            fsrc = src.query_one(Path=file)
            fdst = dst.query_one(Path=file)

            if not fdst or fsrc["Size"] != fdst["Size"]:
                diff_size.append(file)
            else:
                matched_size.append(file)

        return matched_size, diff_size

    def avoid_relist(self):
        # actions: 'new','del','tag','backup','trans','moves'
        # Care?:    N     Y     N     N        Y       Y

        # Actions must go first on both sides since we need tags before
        # transfers
        currA = self.currA0.copy()
        currB = self.currB0.copy()

        for AB in "AB":
            if AB == "A":
                currAB, currBA, BA = currA, currB, "B"
            else:
                currAB, currBA, BA = currB, currA, "A"

            for filename in getattr(self, f"del{AB}"):
                currAB.remove(Path=filename)

            for filenameOLD, filenameNEW in getattr(self, f"moves{AB}"):
                q = currAB.pop({"Path": filenameOLD})
                q["Path"] = filenameNEW
                currAB.add(q)

        for AB in "AB":
            if AB == "A":
                currAB, currBA, BA = currA, currB, "B"
            else:
                currAB, currBA, BA = currB, currA, "A"

            for filename in getattr(self, f"trans{BA}2{AB}"):
                remoteWorkDir = self.api.getRemoteWorkDir(AB) 
                if filename.startswith(remoteWorkDir):  # We don't care about these   ????
                # if filename.startswith(".syncrclone"):  # We don't care about these   ????
                    continue

                q = {"Path": filename}
                if q in currAB:  # Remove the old
                    currAB.remove(q)
                file = currBA[q]
                # file['_copied'] = True # Set this so that on the next run, if using reuse_hashes, it is recomputed
                currAB.add(file)

        return currA, currB


    # def run_shell(self, pre=None):
    #     """Run the shell commands"""
    #     t0 = time.time()
    #     dry = False
    #     import subprocess

    #     cmds = "" if pre else ""
    #     if not cmds:
    #         return

    #     environ = os.environ.copy()
    #     environ["LOGNAME"] = self.logname

    #     kwargs = {}

    #     prefix = "DRY RUN " if dry else ""
    #     if isinstance(cmds, str):
    #         for line in cmds.rstrip().split("\n"):
    #             log(f"{prefix}$ {line}")
    #         shell = True
    #     elif isinstance(cmds, (list, tuple)):
    #         log(f"{prefix}{cmds}")
    #         shell = False
    #     elif isinstance(cmds, dict):
    #         log(f"{prefix}{cmds}")
    #         cmds0 = cmds.copy()
    #         try:
    #             cmds = cmds0.pop("cmd")
    #         except KeyError:
    #             raise KeyError("Dict shell commands MUST have 'cmd' defined")
    #         shell = cmds0.pop("shell", False)
    #         environ.update(cmds0.pop("env", {}))
    #         cmds0.pop("stdout", None)
    #         cmds0.pop("stderr", None)
    #         debug(f"Cleaned cmd: {cmds0}")
    #         kwargs.update(cmds0)
    #     else:
    #         raise TypeError("Shell commands must be str, list/tuple, or dict")

    #     if dry:
    #         return log("DRY-RUN: Not running")

    #     if not pre:
    #         environ["STATS"] = self.stats()

    #     # Apply formatting. Uses the C-Style so that it is less likely to
    #     # have to need escaping
    #     if isinstance(cmds, (list, tuple)):
    #         cmds0 = cmds.copy()
    #         cmds = [cmd % environ for cmd in cmds]
    #         if cmds != cmds0:
    #             debug(f"Formatted cmds: {cmds}")

    #     proc = subprocess.Popen(
    #         cmds,
    #         shell=shell,
    #         env=environ,
    #         stderr=subprocess.PIPE,
    #         stdout=subprocess.PIPE,
    #         **kwargs,
    #     )

    #     out, err = proc.communicate()
    #     out, err = out.decode(), err.decode()
    #     for line in out.split("\n"):
    #         log(f"STDOUT: {line}")

    #     if err.strip():
    #         for line in err.split("\n"):
    #             log(f"STDERR: {line}")
    #     if proc.returncode > 0:
    #         log(f"WARNING: Command return non-zero returncode: {proc.returncode}")
    #     if proc.returncode > 0:
    #         log(f"WARNING: Command return non-zero returncode: {proc.returncode}")
    #         if self._conf.stop_on_shell_error:
    #             raise subprocess.CalledProcessError(proc.returncode, cmds)

    #     self.shell_time += time.time() - t0


    def stats(self):
        txt = [f"A >>> B {self.sumA} | A <<< B {self.sumB}"]
        attrnames = [
            ("New", "new"),
            ("Deleted", "del"),
            # ('Tagged','tag'),
            ("Backed Up", "backup"),
            ("Moved", "moves"),
        ]
        txt.append(
            "A: "
            + " | ".join(
                f'{name} {len(getattr(self,attr + "A"))}' for name, attr in attrnames
            )
        )
        txt.append(
            "B: "
            + " | ".join(
                f'{name} {len(getattr(self,attr + "B"))}' for name, attr in attrnames
            )
        )
        dt = utils.time_format(time.time() - self.t0)
        # dt_shell = utils.time_format(self.shell_time)
        txt.append(f"Time: {dt}")
        return "\n".join(txt)







def syncronization(backendA:Backend, backendB:Backend,Config:dict=None, break_lock=None,**kw)->int:
    """_summary_

    Args:
        backendA (Backend): _description_
        backendB (Backend): _description_
        Config (dict, optional): _description_. Defaults to None.
        break_lock (_type_, optional): _description_. Defaults to None.

    Returns:
        int: 
              0 success
             -1 A is locked
             -2 B is locked 
    """    
    returnValue = [None]
    with tempfile.TemporaryDirectory() as temp_dir:
        print('Temporary directory created:', temp_dir)
        if Config is None:
            Config = configuration
        Sync(workdir=temp_dir,syncConfig=Config, backendA=backendA, backendB=backendB, break_lock=break_lock,returnVal=returnValue)
    print('Temporary directory has been cleaned up')
    return returnValue[0]


    