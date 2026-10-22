# pieces shared by the scripts that build the command dependency graph,
# no matter which file they read (timing_data.json or xia2-debug.txt)
import os, json

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
                if prev_dict['exe_cmd'] == exe_cmd:
                    return prev_dict['curr_poss']

    return None


def find_param_parent(single_par, prev_cmd_lst):
    for key, exe_cmd_lst in PARAM_FROM_PROGRAM:
        if single_par.startswith(key + "="):
            # most recent previous run of any of those programs
            for prev_dict in reversed(prev_cmd_lst):
                if prev_dict['exe_cmd'] in exe_cmd_lst:
                    return prev_dict['curr_poss']

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
    conflict_lst = spread_work_dirs(list_of_commands, dir_of_file, ambiguous_set)
    common_dir = report_n_common_dir(list_of_commands, conflict_lst)
    fill_missing_work_dirs(list_of_commands, common_dir)


def map_abs_paths(list_of_commands):
    dir_of_file = {}
    ambiguous_set = set()
    for cmd_dict in list_of_commands:
        cmd_dict['work_dir'] = None
        for single_par in cmd_dict['par_lst']:
            path_str = reversed_find_str(str_in = single_par, lst_sep_lst = ["="])
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
            if cmd_dict['work_dir'] is not None:
                continue

            no_path_lst = []
            for single_par in cmd_dict['par_lst']:
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
                print("new_work_dir =", new_work_dir)
                cmd_dict['work_dir'] = new_work_dir
                found_new = True
                for file_name in no_path_lst:
                    if (
                        file_name not in dir_of_file
                        and file_name not in ambiguous_set
                    ):
                        dir_of_file[file_name] = cmd_dict['work_dir']

            elif len(dir_set) > 1 and cmd_dict not in conflict_lst:
                conflict_lst.append(cmd_dict)


    return conflict_lst


def report_n_common_dir(list_of_commands, conflict_lst):
    common_dir = None
    for cmd_dict in list_of_commands:
        if cmd_dict['work_dir'] is None:
            # not implied by the log, left as None on purpose
            if cmd_dict in conflict_lst:
                print("Dir not found (conflicting dirs) for:",
                      cmd_dict['curr_poss'], cmd_dict['exe_cmd'])

            else:
                print("Dir not found for:",
                      cmd_dict['curr_poss'], cmd_dict['exe_cmd'])

        elif common_dir is None:
            common_dir = cmd_dict['work_dir']

        else:
            common_dir = os.path.commonpath([common_dir, cmd_dict['work_dir']])


    return common_dir

def fill_missing_work_dirs(list_of_commands, common_dir):
    print("\n using:", common_dir, "as root path")
    natv_posi = common_dir.rfind("NATIVE") - 1
    extra_dir_left = common_dir[0: natv_posi]

    for cmd_dict in list_of_commands:
        if cmd_dict['work_dir'] is None:
            cmd_dict['work_dir'] = extra_dir_left + "/scale/"


def full_path_lst(file_lst, cmd_dict):
    # file names (no path) of a command -> the same files with full path,
    # as written in the command if it was there, otherwise in 'work_dir',
    # without 'work_dir' the name stays as it is (no path)
    path_lst = []
    for file_name in file_lst:
        if cmd_dict['work_dir'] is None:
            new_path = file_name

        else:
            new_path = os.path.join(cmd_dict['work_dir'], file_name)

        for single_par in cmd_dict['par_lst']:
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
    fmt_str = "%-5s %-30s %-5s %-30s %s"
    print("\n", "=" * 90)
    print(fmt_str % ("from", "parent", "to", "child", "via"))
    print("-" * 90)
    for cmd_dict in list_of_commands:
        for parent_poss in sorted(cmd_dict['parent_pos_lst']):
            lbl_lst = []
            for label in cmd_dict['files_from_parent_dict'][parent_poss]:
                lbl_lst.append(short_label(label))

            print(fmt_str % (
                parent_poss, list_of_commands[parent_poss]['exe_cmd'],
                cmd_dict['curr_poss'], cmd_dict['exe_cmd'],
                ", ".join(sorted(lbl_lst)),
            ))

    print("=" * 90)


def export_reusable_graph_list(all_dat):
    with open("run_data", "w") as fp:
        json.dump(all_dat, fp, indent=4)


def build_reusable_graph_list(list_of_commands = None, bigger_lin_in = 0):
    lst_nod = []
    bigger_lin = bigger_lin_in
    for cmd_dict in list_of_commands:
        if bigger_lin < cmd_dict['curr_poss']:
            bigger_lin = cmd_dict['curr_poss']

        parent_node_lst = cmd_dict['parent_pos_lst']

        print("parent_node_lst =", parent_node_lst)

        if  parent_node_lst == []:
            print("adding ref to node [0]")
            parent_node_lst = [0]

        node = {
            "_base_dir"             :os.getcwd(),
            "cmd_dict_ini"          :{
                                        "nod_lst":[None],
                                        "cmd_lst":[[None]]
                                    },
            "full_cmd_lst"          :cmd_dict['exe_cmd'],
            "lst2run"               :[[cmd_dict['exe_cmd']]],

            "_lst_expt_in"          :full_path_lst(
                                        cmd_dict['expt_from_prev_lst'], cmd_dict
                                    ),
            "_lst_refl_in"          :full_path_lst(
                                        cmd_dict['refl_from_prev_lst'], cmd_dict
                                    ),
            "_lst_expt_out"         :full_path_lst(
                                        cmd_dict['expt_for_next_lst'], cmd_dict
                                    ),
            "_lst_refl_out"         :full_path_lst(
                                        cmd_dict['refl_for_next_lst'], cmd_dict
                                    ),
            "_run_dir"              :cmd_dict['work_dir'],

            "_html_rep"             :None,
            "_predic_refl"          :None,
            "log_file_path"         :cmd_dict.get('log_file_path'),
            "number"                :cmd_dict['curr_poss'],
            "parent_node_lst"       :parent_node_lst,
            "child_node_lst"        :cmd_dict['chidren_pos_lst'],
            "status"                :"Succeeded"
        }
        lst_nod.append(node)


    all_dat = {
            "step_list"             :lst_nod,
            "bigger_lin"            :bigger_lin,
    }
    return all_dat

