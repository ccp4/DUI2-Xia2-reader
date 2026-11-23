# pieces shared by the scripts that build the command dependency graph,
# no matter which file they read (timing_data.json or xia2-debug.txt)
import os, json, shutil, subprocess

if __package__:
    from dui2.server.data_n_json import spit_out, get_phil_master

else:
    def spit_out(str_out = " None ", req_obj = None, out_type = None):
        print(str_out, end = "")

    def get_phil_master(cmd_str):
        # without DUI2 (standalone script) parameter names stay as given
        return None



# parts of a parameter name (before "=") that mark it as a written file
# e.g.: output.experiments=..., mtz.hklout=..., json=...
OUTPUT_KEY_PARTS = ["output", "hklout", "json"]

# CCP4 style positional pairs, e.g.: freerflag 'hklin' 'a.mtz' 'hklout' 'b.mtz'
POSITIONAL_IN = ["hklin", "xyzin"]
POSITIONAL_OUT = ["hklout", "xyzout"]

FILE_EXTENSIONS = [
    ".expt", ".refl", ".mtz", ".mmcif", ".cif", ".json", ".phil",
    ".mask", ".sca", ".p4p", ".html", ".log", ".bz2",
]

# parameters whose value is a result of a previous program, not a file,
# (parameter name, programs whose most recent run gave that value)
PARAM_FROM_PROGRAM = [
    (
        "change_of_basis_op",
        ["dials.refine_bravais_settings", "dials.symmetry"],
    ),
    ("space_group", ["dials.refine_bravais_settings", "dials.symmetry"]),
    ("cut_data.d_min", ["dials.estimate_resolution"]),
]


def reversed_find_str(str_in = None, lst_sep_lst = ["=", os.sep]):
    # position of the last separator, -1 if there is none
    cut = -1
    for sep in lst_sep_lst:
        cut = max(cut, str_in.rfind(sep))

    return str_in[cut + 1:]


def is_dials_command(exe_cmd):
    # filtering and allowing only dials commands to pass
    program_name = reversed_find_str(str_in = exe_cmd, lst_sep_lst = [os.sep])
    DO_starts_with_dials = bool(program_name.startswith("dials."))
    return DO_starts_with_dials


def is_output_key(key):
    if key.endswith("hklout"):
        return True

    for part in key.split("."):
        if part in OUTPUT_KEY_PARTS:
            return True

    return False


def has_file_extension(file_name):
    for ext in FILE_EXTENSIONS:
        if file_name.endswith(ext):
            return True

    return False


def is_bravais_setting(file_name):
    prefix, suffix = "bravais_setting_", ".expt"
    if not (file_name.startswith(prefix) and file_name.endswith(suffix)):
        return False

    return file_name[len(prefix):-len(suffix)].isdecimal()


def bravais_solution_num(file_lst):
    # DUI2 (CmdNode.set_in_fil_n_par) goes from the solution number
    # (position in the reindex table) to the file: 3 -> bravais_setting_3.expt
    # here it goes the other way: bravais_setting_3.expt -> "3",
    # None when no bravais_setting_N.expt is in the list
    prefix, suffix = "bravais_setting_", ".expt"
    for file_str in file_lst:
        file_name = reversed_find_str(str_in = file_str)
        if is_bravais_setting(file_name):
            return str(int(file_name[len(prefix):-len(suffix)]))

    return None


# xia2 (Experts/LatticeExpert.py) gives dials.reindex "space_group=<number>",
# the lowest symmetry space group of the chosen lattice
SPACEGROUP_TO_LATTICE = {
    "1": "aP", "3": "mP", "5": "mC", "16": "oP", "20": "oC", "22": "oF",
    "23": "oI", "75": "tP", "79": "tI", "143": "hP", "146": "hR",
    "195": "cP", "196": "cF", "197": "cI",
}


def get_program_name(exe_cmd):
    return reversed_find_str(str_in = exe_cmd, lst_sep_lst = [os.sep])


def get_param_value(par_lst, key):
    # "key=value" in par_lst -> "value", None when the key is not there
    for single_par in par_lst:
        if single_par.startswith(key + "="):
            return single_par[len(key) + 1:].strip()

    return None


def solution_from_bravais_summary(cmd_dict, prev_cmd_lst):
    # xia2 (Modules/Indexer/DialsIndexer.py) does NOT give the chosen
    # bravais_setting_N.expt to dials.reindex, it loads that file itself and
    # reindexes only the reflections with:
    #   change_of_basis_op=<cb_op of N>  space_group=<number of N's lattice>
    # so, like DUI2 does (solution number -> cb_op), but the other way,
    # N is the entry of bravais_summary.json with that cb_op and lattice
    cb_op = get_param_value(cmd_dict["par_lst"], "change_of_basis_op")
    if cb_op is None:
        return None

    rbs_dict = None
    for prev_dict in reversed(prev_cmd_lst):
        if get_program_name(prev_dict["exe_cmd"]) == (
            "dials.refine_bravais_settings"
        ):
            rbs_dict = prev_dict
            break

    if rbs_dict is None:
        return None

    # xia2 runs dials.refine_bravais_settings and this dials.reindex
    # in the same directory
    work_dir = rbs_dict["work_dir"]
    if work_dir is None:
        work_dir = cmd_dict["work_dir"]

    if work_dir is None:
        return None

    try:
        with open(os.path.join(work_dir, "bravais_summary.json")) as fp:
            summary_dict = json.load(fp)

    except (OSError, ValueError):
        return None

    lattice = SPACEGROUP_TO_LATTICE.get(
        get_param_value(cmd_dict["par_lst"], "space_group")
    )
    found_lst = []
    for sol_num, sol_dict in summary_dict.items():
        if str(sol_dict.get("cb_op")).replace(" ", "") != cb_op.replace(" ", ""):
            continue

        if lattice is not None and sol_dict.get("bravais") != lattice:
            continue

        found_lst.append(sol_num)

    if len(found_lst) == 1:
        return str(int(found_lst[0]))

    return None


def solution_from_next_cmd(cmd_dict, next_cmd_lst):
    # the bravais_setting_N.expt that xia2 loaded travels to the next
    # command (e.g.: dials.refine) together with the reindexed reflections
    for next_dict in next_cmd_lst:
        if any(
            refl_name in next_dict["refl_from_prev_lst"]
            for refl_name in cmd_dict["refl_for_next_lst"]
        ):
            sol_set = set()
            for file_str in next_dict["expt_from_prev_lst"]:
                sol_num = bravais_solution_num([file_str])
                if sol_num is not None:
                    sol_set.add(sol_num)

            if len(sol_set) == 1:
                return sol_set.pop()

            return None

    return None


# xia2 (like any DIALS user) may give a parameter with a shortened name,
# e.g.: nproc=4 or d_min=1.5, DIALS (libtbx.phil) finds the full path itself,
# but the DUI2 parameter widgets only know the full PHIL path (local_path),
# e.g.: spotfinder.mp.nproc=4, so a node imported from xia2 must have the
# full path in << lst2run >>, otherwise the client (update_all_param) finds
# no widget to update and the parameters area keeps the defaults, even if
# cloning the node (which copies << lst2run >> as text) runs correctly

_FULL_PATHS_CACHE = {}


def collect_full_paths(phil_obj_lst, path_set):
    # same walk as BuildParamDictData.deep_in_recurs in data_n_json
    for single_obj in phil_obj_lst:
        if getattr(single_obj, "is_definition", False):
            path_set.add(str(single_obj.full_path()))

        elif getattr(single_obj, "is_scope", False):
            collect_full_paths(single_obj.objects, path_set)


def phil_full_paths(program_name):
    # e.g.: "dials.find_spots" -> {"spotfinder.mp.nproc", ...},
    # empty set for commands without PHIL scope in DUI2 (e.g.: dials.export)
    if program_name not in _FULL_PATHS_CACHE:
        path_set = set()
        if program_name.startswith("dials."):
            master = get_phil_master(program_name[len("dials."):])
            collect_full_paths(getattr(master, "objects", []), path_set)

        _FULL_PATHS_CACHE[program_name] = path_set

    return _FULL_PATHS_CACHE[program_name]


def to_full_phil_path(single_par, path_set):
    # "nproc=4" -> "spotfinder.mp.nproc=4", like libtbx.phil does, a short
    # name is the last part(s) of the full path, when it is ambiguous or
    # not found the parameter stays as given
    if "=" not in single_par or len(path_set) == 0:
        return single_par

    key, value = single_par.split("=", 1)
    key = key.strip()
    if key in path_set:
        return key + "=" + value.strip()

    found_lst = [
        full_path for full_path in path_set
        if full_path.endswith("." + key)
    ]
    if len(found_lst) == 1:
        return found_lst[0] + "=" + value.strip()

    return single_par


def dui2_params_lst(cmd_dict):
    # parameters of a xia2 command as DUI2 would have written them
    path_set = phil_full_paths(get_program_name(cmd_dict["exe_cmd"]))
    return [
        to_full_phil_path(single_par, path_set)
        for single_par in cmd_dict.get("tuning_params_lst", [])
    ]


def build_cmd_dict_ini(cmd_dict, parent_node_lst, prev_cmd_lst, next_cmd_lst):
    # the request DUI2 would have sent to run the same step,
    # e.g.: {"nod_lst": [4], "cmd_lst": [["dials.reindex", "3"]]}
    if get_program_name(cmd_dict["exe_cmd"]) == "dials.reindex":
        sol_num = bravais_solution_num(cmd_dict["expt_from_prev_lst"])
        if sol_num is None:
            sol_num = solution_from_bravais_summary(cmd_dict, prev_cmd_lst)

        if sol_num is None:
            sol_num = solution_from_next_cmd(cmd_dict, next_cmd_lst)

        if sol_num is not None:
            return {
                "nod_lst":list(parent_node_lst),
                "cmd_lst":[["dials.reindex", sol_num]]
            }

        # NOT tied to dials.refine_bravais_settings (e.g.: xia2 reindexing
        # after dials.integrate or dials.symmetry), same request DUI2 sends
        # from the << optional >> widget: the parameters as they were given
        return {
            "nod_lst":list(parent_node_lst),
            "cmd_lst":[["dials.reindex"] + dui2_params_lst(cmd_dict)]
        }

    return {"nod_lst":[None], "cmd_lst":[[None]]}


def is_bravais_summary(file_name):
    return file_name == "bravais_summary.json"


# files written with default names that never appear as output parameters,
# (test on the file name, program that wrote it)
IMPLICIT_OUTPUTS = [
    (is_bravais_setting, "dials.refine_bravais_settings"),
    (is_bravais_summary, "dials.refine_bravais_settings"),
]


def find_implicit_parent(file_name, prev_cmd_lst):
    for matches, exe_cmd in IMPLICIT_OUTPUTS:
        if matches(file_name):
            # most recent previous run of that program
            for prev_dict in reversed(prev_cmd_lst):
                if prev_dict["exe_cmd"] == exe_cmd:
                    return prev_dict["curr_poss"]

    return None


def find_param_parent(single_par, prev_cmd_lst):
    for key, exe_cmd_lst in PARAM_FROM_PROGRAM:
        if single_par.startswith(key + "="):
            # most recent previous run of any of those programs
            for prev_dict in reversed(prev_cmd_lst):
                if prev_dict["exe_cmd"] in exe_cmd_lst:
                    return prev_dict["curr_poss"]

    return None


def short_label(label):
    # parameter values keep their name, files lose path and "key="
    for key, exe_cmd_lst in PARAM_FROM_PROGRAM:
        if label.startswith(key + "="):
            return label

    return reversed_find_str(str_in = label)


def find_work_dirs(list_of_commands):
    # sets cmd_dict['work_dir'], the directory where each command ran,
    # using only directories that the logged command lines imply,
    # it stays None when the log does not tell it
    #
    # xia2 removes "<working directory>/" from the arguments it logs, so:
    #  - a file read from another directory keeps its full path, e.g.:
    #      dials.index  ... 'output.experiments=5_indexed.expt'
    #      dials.refine '/full/path/.../index/5_indexed.expt' ...
    #    tells that dials.index ran in /full/path/.../index

    dir_of_file, ambiguous_set = map_abs_paths(list_of_commands)
    conflict_lst = spread_work_dirs(
        list_of_commands, dir_of_file, ambiguous_set
    )
    common_dir = report_n_common_dir(list_of_commands, conflict_lst)
    fill_missing_work_dirs(list_of_commands, common_dir)


def map_abs_paths(list_of_commands):
    dir_of_file = {}
    ambiguous_set = set()
    for cmd_dict in list_of_commands:
        cmd_dict["work_dir"] = None
        for single_par in cmd_dict["par_lst"]:
            path_str = reversed_find_str(
                str_in = single_par, lst_sep_lst = ["="]
            )
            if os.path.isabs(path_str):
                file_name = reversed_find_str(str_in = path_str)
                dir_name = os.path.dirname(os.path.normpath(path_str))
                if file_name in ambiguous_set:
                    continue

                elif file_name not in dir_of_file:
                    dir_of_file[file_name] = dir_name

                elif dir_of_file[file_name] != dir_name:
                    del dir_of_file[file_name]
                    ambiguous_set.add(file_name)

    return dir_of_file, ambiguous_set


def spread_work_dirs(list_of_commands, dir_of_file, ambiguous_set):
    conflict_lst = []
    found_new = True
    while found_new:
        found_new = False
        for cmd_dict in list_of_commands:
            if cmd_dict["work_dir"] is not None:
                continue

            no_path_lst = []
            for single_par in cmd_dict["par_lst"]:
                path_str = reversed_find_str(
                    str_in = single_par, lst_sep_lst = ["="]
                )
                if has_file_extension(path_str) and os.sep not in path_str:
                    no_path_lst.append(path_str)

            # every directory the files of this command point to
            dir_set = set()
            for file_name in no_path_lst:
                if file_name in dir_of_file:
                    dir_set.add(dir_of_file[file_name])

            if len(dir_set) == 1:
                new_work_dir = dir_set.pop()
                #print("new_work_dir =", new_work_dir)
                cmd_dict["work_dir"] = new_work_dir
                found_new = True
                for file_name in no_path_lst:
                    if (
                        file_name not in dir_of_file
                        and file_name not in ambiguous_set
                    ):
                        dir_of_file[file_name] = cmd_dict["work_dir"]

            elif len(dir_set) > 1 and cmd_dict not in conflict_lst:
                conflict_lst.append(cmd_dict)

    return conflict_lst


def report_n_common_dir(list_of_commands, conflict_lst):
    common_dir = None
    for cmd_dict in list_of_commands:
        if cmd_dict["work_dir"] is None:
            # not implied by the log, left as None on purpose
            if cmd_dict in conflict_lst:
                print("Dir not found (conflicting dirs) for:",
                      cmd_dict["curr_poss"], cmd_dict["exe_cmd"])

            else:
                tmp_off = """print("Dir not found for:",
                      cmd_dict['curr_poss'], cmd_dict['exe_cmd']
                )"""

        elif common_dir is None:
            common_dir = cmd_dict["work_dir"]

        else:
            common_dir = os.path.commonpath([common_dir, cmd_dict["work_dir"]])


    return common_dir

def fill_missing_work_dirs(list_of_commands, common_dir):
    print("\n using:", common_dir, "as root path")
    natv_posi = common_dir.rfind("NATIVE") - 1
    extra_dir_left = common_dir[0: natv_posi]

    for cmd_dict in list_of_commands:
        if cmd_dict["work_dir"] is None:
            cmd_dict["work_dir"] = extra_dir_left + "/scale/"


# DUI2 grows its tree one step after another: a new node continues from the
# node it was launched from and inherits from it (and, through it, from all
# older nodes) every file it does not write itself, see CmdNode.__init__ and
# CmdNode.set_exe_files_out in multi_node.py, so in DUI2 only
# dials.combine_experiments has more than one parent
#
# the parents found through files are not like that, e.g.: dials.index reads
# imported.expt (from dials.import) and strong.refl (from dials.find_spots),
# so when a command has more than one parent only the last one is kept

JOIN_COMMANDS = ["dials.combine_experiments"]


def to_dui2_tree(list_of_commands, root_poss):
    # must run after the file connections (parent_pos_lst) are set, it
    # replaces 'parent_pos_lst' and 'chidren_pos_lst', the file connections
    # stay in 'file_parent_pos_lst'
    cmd_by_poss = {}
    for cmd_dict in list_of_commands:
        cmd_dict["file_parent_pos_lst"] = list(cmd_dict["parent_pos_lst"])
        cmd_dict["chidren_pos_lst"] = []
        cmd_by_poss[cmd_dict["curr_poss"]] = cmd_dict

        if cmd_dict["parent_pos_lst"] == []:
            # e.g.: dials.import, hangs from the node that imported xia2
            cmd_dict["parent_pos_lst"] = [root_poss]

        elif cmd_dict["exe_cmd"] not in JOIN_COMMANDS:
            cmd_dict["parent_pos_lst"] = [max(cmd_dict["parent_pos_lst"])]

    for cmd_dict in list_of_commands:
        for parent_poss in cmd_dict["parent_pos_lst"]:
            if parent_poss in cmd_by_poss:
                cmd_by_poss[parent_poss]["chidren_pos_lst"].append(
                    cmd_dict["curr_poss"]
                )


def full_path_lst(file_lst, cmd_dict):
    # file names (no path) of a command -> the same files with full path,
    # as written in the command if it was there, otherwise in 'work_dir',
    # without 'work_dir' the name stays as it is (no path)
    path_lst = []
    for file_name in file_lst:
        if cmd_dict["work_dir"] is None:
            new_path = file_name

        else:
            new_path = os.path.join(cmd_dict["work_dir"], file_name)

        for single_par in cmd_dict["par_lst"]:
            path_str = reversed_find_str(str_in = single_par, lst_sep_lst = ["="])
            if (
                os.path.isabs(path_str)
                and reversed_find_str(str_in = path_str) == file_name
            ):
                new_path = path_str
                break

        path_lst.append(os.path.normpath(new_path))

    return path_lst


def print_graph_table(list_of_commands):
    # one row per connection, same format for every input file type
    print("\n", "=" * 90)
    print("from", "parent", "to", "child", "via")
    print("-" * 90)
    for cmd_dict in list_of_commands:
        for parent_poss in sorted(cmd_dict["parent_pos_lst"]):
            lbl_lst = []
            FIXME = """
            fmt_str = "%-5s %-30s %-5s %-30s %s"
            for label in cmd_dict['files_from_parent_dict'][parent_poss]:
                lbl_lst.append(short_label(label))

            print(fmt_str % (
                parent_poss, list_of_commands[parent_poss]['exe_cmd'],
                cmd_dict['curr_poss'], cmd_dict['exe_cmd'],
                ", ".join(sorted(lbl_lst)),
            ))
            """
            fmt_str = "%-5s %-30s %-5s %-30s"
            print(fmt_str % (
                parent_poss, list_of_commands[parent_poss]["exe_cmd"],
                cmd_dict["curr_poss"], cmd_dict["exe_cmd"]
            ))

    print("=" * 90)


def export_reusable_graph_list(all_dat):
    with open("run_data", "w") as fp:
        json.dump(all_dat, fp, indent=4)


def build_reusable_graph_list(
    list_of_commands = None, bigger_lin_in = 0, import_lin = 0
):
    lst_nod = []
    bigger_lin = bigger_lin_in
    for cmd_num, cmd_dict in enumerate(list_of_commands):
        if bigger_lin < cmd_dict["curr_poss"]:
            bigger_lin = cmd_dict["curr_poss"]

        parent_node_lst = cmd_dict["parent_pos_lst"]

        if  parent_node_lst == []:
            parent_node_lst = [bigger_lin - 1]
            print("parent_node_lst(fixed) =", parent_node_lst)

        lst_expt_in = full_path_lst(cmd_dict["expt_from_prev_lst"], cmd_dict)
        lst_refl_in = full_path_lst(cmd_dict["refl_from_prev_lst"], cmd_dict)
        lst_expt_out = full_path_lst(cmd_dict["expt_for_next_lst"], cmd_dict)
        lst_refl_out = full_path_lst(cmd_dict["refl_for_next_lst"], cmd_dict)
        # like CmdNode.set_exe_files_out, a step that writes no .expt/.refl
        # hands on what it read, so a DUI2 step launched from it gets files
        if lst_expt_out == []:
            lst_expt_out = list(lst_expt_in)

        if lst_refl_out == []:
            lst_refl_out = list(lst_refl_in)

        # what the client shows in the parameters area and copies when
        # cloning: program name (no path) + parameters with full PHIL path,
        # the same as a node launched from DUI2 (CmdNode.set_in_fil_n_par)
        program_name = get_program_name(cmd_dict["exe_cmd"])
        lst2run = [[program_name] + dui2_params_lst(cmd_dict)]
        cmd_dict_ini = build_cmd_dict_ini(
            cmd_dict, parent_node_lst,
            list_of_commands[0:cmd_num],
            list_of_commands[cmd_num + 1:]
        )
        if cmd_dict_ini["cmd_lst"][0][0] == "dials.reindex":
            # same as DUI2 (CmdNode.set_in_fil_n_par): the client reads
            # the solution number (row + 1) from lst2run[-1][1] (cmd2show)
            # or, for a << generic >> dials.reindex, the parameters it shows
            # in the << optional >> widget
            lst2run = [list(cmd_dict_ini["cmd_lst"][0])]

        node = {
            "_base_dir"             :os.getcwd(),
            "cmd_dict_ini"          :cmd_dict_ini,
            # list of commands (each one a list), like CmdNode.full_cmd_lst,
            # e.g.: for the << history >> request, a plain string here
            # would be iterated one character at a time
            "full_cmd_lst"          :[
                [cmd_dict["exe_cmd"]] + list(cmd_dict["par_lst"])
            ],
            "lst2run"               :lst2run,
            "_lst_expt_in"          :lst_expt_in,
            "_lst_refl_in"          :lst_refl_in,
            "_lst_expt_out"         :lst_expt_out,
            "_lst_refl_out"         :lst_refl_out,
            "_run_dir"              :cmd_dict["work_dir"],

            "_html_rep"             :None,
            "_predic_refl"          :None,
            "log_file_path"         :cmd_dict.get("log_file_path"),
            "number"                :cmd_dict["curr_poss"],
            "parent_node_lst"       :parent_node_lst,
            "child_node_lst"        :cmd_dict["chidren_pos_lst"],
            "status"                :"Succeeded"
        }
        lst_nod.append(node)


    all_dat = {
            "step_list"             :lst_nod,
            "bigger_lin"            :bigger_lin,
    }
    return all_dat



def send_line(new_line, req_obj):
    # like add_log_line in multi_node, returns 1 when the client is gone
    try:
        spit_out(str_out = new_line, req_obj = req_obj, out_type = "utf-8")

    except BrokenPipeError:
        return 1

    return 0


def run_n_spit_out(lst_dat_in, run_dir, req_obj):
    # runs one command in run_dir, sending each line it prints as it comes,
    # returns the number of lines that could not be sent (BrokenPipe)

    print("\n\n lst_dat_in =", lst_dat_in, "\n\n")

    n_broken_pipes = 0
    exe_path = shutil.which(lst_dat_in[0])
    if exe_path is None:
        new_line = "command not found: " + str(lst_dat_in[0]) + "\n"
        return send_line(new_line, req_obj)

    lst_dat_in[0] = str(exe_path)
    run_proc = subprocess.Popen(
        lst_dat_in,
        shell = False,
        cwd = run_dir,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        universal_newlines = True
    )
    new_line = None
    while run_proc.poll() is None or new_line != "":
        new_line = run_proc.stdout.readline()
        if new_line != "":
            n_broken_pipes += send_line(new_line, req_obj)

    run_proc.stdout.close()
    return n_broken_pipes


def run_predicts_n_reports(all_dat = None, arg_in = None, req_obj = None):

    #TODO: look if the arg_in variable should be used here

    lst_cmd = all_dat["step_list"]
    # every report and prediction goes in the same dir, with the node number
    # in the file name, the commands run there (cwd) without os.chdir
    path_to_dir = os.path.abspath("predicts_n_reports")
    print("path_to_dir =", path_to_dir)
    try:
        os.mkdir(path_to_dir)

    except FileExistsError:
        print("NOT creating again: ", path_to_dir)

    n_broken_pipes = 0
    for cmd_node in lst_cmd:
        number_str = str(cmd_node["number"])
        new_line = (
            "HTML Report + Reflection Prediction (node " + number_str +
            ") ... START\n"
        )
        n_broken_pipes += send_line(new_line, req_obj)

        # running HTML report generation
        rep_lst_dat_in = ["dials.report"]
        for expt_2_add in cmd_node["_lst_expt_out"]:
            rep_lst_dat_in.append(expt_2_add)

        for refl_2_add in cmd_node["_lst_refl_out"]:
            rep_lst_dat_in.append(refl_2_add)

        output_html_file = "dials.report_n_" + number_str + ".html"
        rep_lst_dat_in.append("output.html=" + output_html_file)
        n_broken_pipes += send_line(str(rep_lst_dat_in) + "\n", req_obj)
        n_broken_pipes += run_n_spit_out(rep_lst_dat_in, path_to_dir, req_obj)

        tmp_html_path = path_to_dir + os.sep + output_html_file
        if os.path.exists(tmp_html_path):
            cmd_node["_html_rep"] = str(tmp_html_path)
            new_line = "HTML Report ready\n"

        else:
            new_line = "No need for HTML report in this step\n"

        n_broken_pipes += send_line(new_line, req_obj)

        # running prediction generation, like CmdNode.generate_predict_n_report
        # but with the node number in the output name (all share one dir)
        if cmd_node["_lst_expt_out"] == []:
            new_line = "No experiments, no Reflection Prediction here\n"
            n_broken_pipes += send_line(new_line, req_obj)

        else:
            pred_lst_dat_in = ["dials.predict"]
            for expt_2_add in cmd_node["_lst_expt_out"]:
                pred_lst_dat_in.append(expt_2_add)

            output_pred_file = "predicted_n_" + number_str + ".refl"
            pred_lst_dat_in.append("output=" + output_pred_file)
            n_broken_pipes += send_line(str(pred_lst_dat_in) + "\n", req_obj)
            n_broken_pipes += run_n_spit_out(
                pred_lst_dat_in, path_to_dir, req_obj
            )

            tmp_predic_path = path_to_dir + os.sep + output_pred_file
            if os.path.exists(tmp_predic_path):
                cmd_node["_predic_refl"] = str(tmp_predic_path)
                new_line = "Reflection Prediction ready\n"

            else:
                new_line = "No need for Reflection Prediction in this step\n"

            n_broken_pipes += send_line(new_line, req_obj)

        new_line = (
            "HTML Report + Reflection Prediction (node " + number_str +
            ") ... END\n"
        )
        n_broken_pipes += send_line(new_line, req_obj)

    if n_broken_pipes > 0:
        print("<< BrokenPipe err catch >> while sending output")

    all_dat["step_list"] = lst_cmd
    print("\n done \n")
    return all_dat
