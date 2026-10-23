import sys, os, shutil, subprocess

try:
    from graph_common import (
        reversed_find_str, is_output_key, has_file_extension,
        is_bravais_setting, find_implicit_parent, find_param_parent,
        print_graph_table, export_reusable_graph_list,
        build_reusable_graph_list, is_dials_command, find_work_dirs,
        POSITIONAL_IN, POSITIONAL_OUT
    )

except ModuleNotFoundError:
    from dui2.server.xia2_reader.graph_common import (
        reversed_find_str, is_output_key, has_file_extension,
        is_bravais_setting, find_implicit_parent, find_param_parent,
        print_graph_table, export_reusable_graph_list,
        build_reusable_graph_list, is_dials_command, find_work_dirs,
        POSITIONAL_IN, POSITIONAL_OUT
    )

def split_cmd_line(new_cmd_str):
    new_cmd_str = new_cmd_str.strip()
    divide_pos = new_cmd_str.find("'")
    if divide_pos == -1:
        # command without arguments, e.g.: xia2.report
        return new_cmd_str, [new_cmd_str]

    exe_cmd = new_cmd_str[0:divide_pos].strip()
    per_line_cmd_lst = new_cmd_str[divide_pos + 1:-1].split("' '")

    full_cmd_lst = [exe_cmd]
    for inner_cmd in per_line_cmd_lst:
        if inner_cmd == "":
            pass

        elif inner_cmd not in full_cmd_lst:
            full_cmd_lst.append(inner_cmd)

    return exe_cmd, full_cmd_lst


def classify_params(full_cmd_lst):
    connect_from_prev_lst = []
    connect_for_next_lst = []
    tuning_params_lst = []
    par_lst = full_cmd_lst[1:]
    skip_next = False
    for pos, single_par in enumerate(par_lst):
        if skip_next:
            skip_next = False

        elif (
            single_par in POSITIONAL_IN + POSITIONAL_OUT
            and pos + 1 < len(par_lst)
        ):
            if single_par in POSITIONAL_OUT:
                connect_for_next_lst.append(par_lst[pos + 1])

            else:
                connect_from_prev_lst.append(par_lst[pos + 1])

            skip_next = True

        elif "=" in single_par:
            key = single_par[0:single_par.find("=")]
            file_name = reversed_find_str(str_in = single_par)
            if is_output_key(key) and "." in file_name:
                connect_for_next_lst.append(single_par)

            elif "input" in key or has_file_extension(file_name):
                connect_from_prev_lst.append(single_par)

            else:
                # includes non file outputs like output.project_name=...
                tuning_params_lst.append(single_par)

        elif has_file_extension(reversed_find_str(str_in = single_par)):
            connect_from_prev_lst.append(single_par)

        else:
            tuning_params_lst.append(single_par)

    return connect_from_prev_lst, connect_for_next_lst, tuning_params_lst


def split_by_type(par_lst):
    expt_lst = []
    refl_lst = []
    another_lst = []
    for par in par_lst:
        par = reversed_find_str(str_in = par)
        if par[-5:] == ".refl":
            refl_lst.append(par)

        elif par[-5:] == ".expt":
            expt_lst.append(par)

        else:
            another_lst.append(par)

    return expt_lst, refl_lst, another_lst


def add_connection(parent_dict, child_dict, file_name):
    parent_poss = parent_dict['curr_poss']
    if parent_poss not in child_dict['parent_pos_lst']:
        child_dict['parent_pos_lst'].append(parent_poss)
        child_dict['files_from_parent_dict'][parent_poss] = []
        parent_dict['chidren_pos_lst'].append(child_dict['curr_poss'])

    child_dict['files_from_parent_dict'][parent_poss].append(file_name)


# xia2 (DefaultDriver.close_wait) echoes the end of every log it closes:
#   Last 50 lines of /full/path/5_dials.index.log:
#   <log line> ... # command line: ... # timing information: ...
LOG_HEADER_START = "Last "
LOG_HEADER_MID = " lines of "

# where the log contents read from xia2-debug.txt are written
LOG_COPY_DIR = "xia2_logs"


def find_log_block(lines_str, cmd_line_pos):
    # finds the echoed log that contains the "# command line:" at
    # cmd_line_pos, returns (original log path, list of its lines),
    # (None, []) when the command line is not inside an echoed log
    for header_pos in range(cmd_line_pos - 1, -1, -1):
        header = lines_str[header_pos].rstrip("\n")
        if (
            header.startswith(LOG_HEADER_START)
            and LOG_HEADER_MID in header
            and header.endswith(":")
        ):
            num_str = header[len(LOG_HEADER_START):header.find(LOG_HEADER_MID)]
            if not num_str.isdecimal():
                continue

            num_lines = int(num_str)
            if cmd_line_pos > header_pos + num_lines:
                # the closest echoed log ends before this command line
                return None, []

            log_path = header[header.find(LOG_HEADER_MID) + len(LOG_HEADER_MID):-1]
            return log_path, lines_str[header_pos + 1:header_pos + 1 + num_lines]

    return None, []


def write_log_copy(log_path, log_lines):
    # writes the echoed log lines into LOG_COPY_DIR, keeping the xia2 name
    # (e.g.: 5_dials.index.log, unique thanks to the xia2 run number)
    os.makedirs(LOG_COPY_DIR, exist_ok = True)
    new_path = os.path.abspath(
        os.path.join(LOG_COPY_DIR, os.path.basename(log_path))
    )
    with open(new_path, "w", encoding="utf-8") as fp:
        fp.writelines(log_lines)

    return new_path


def get_list_of_commands(path_in, bigger_lin_in = 0):
    print("file 2 read = ", path_in)


    log_file = open(path_in, 'r', encoding="utf-8")
    lines_str = log_file.readlines()
    log_file.close()
    list_of_commands = []
    curr_poss = bigger_lin_in

    print("curr_poss(get_list_of_commands)=", curr_poss)

    for position, single_line in enumerate(lines_str):
        if single_line[0:15] == "# command line:":
            new_cmd_str = lines_str[position + 1][1:]
            exe_cmd, full_cmd_lst = split_cmd_line(new_cmd_str)

            if not is_dials_command(exe_cmd) or exe_cmd == 'dials.report':
                #TODO: have a look if some command escapes this
                continue

            (
                connect_from_prev_lst, connect_for_next_lst, tuning_params_lst
            ) = classify_params(full_cmd_lst)

            (
                expt_from_prev_lst, refl_from_prev_lst, another_from_prev_lst
            ) = split_by_type(connect_from_prev_lst)

            (
                expt_for_next_lst, refl_for_next_lst, another_for_next_lst
            ) = split_by_type(connect_for_next_lst)

            log_file_path = None
            orig_log_path, log_lines = find_log_block(lines_str, position)
            if orig_log_path is not None:
                log_file_path = write_log_copy(orig_log_path, log_lines)

            cmd_dict = {
                'full_cmd_lst'              :full_cmd_lst,
                'exe_cmd'                   :exe_cmd,
                'par_lst'                   :full_cmd_lst[1:],
                'expt_from_prev_lst'        :expt_from_prev_lst,
                'refl_from_prev_lst'        :refl_from_prev_lst,
                'another_from_prev_lst'     :another_from_prev_lst,
                'expt_for_next_lst'         :expt_for_next_lst,
                'refl_for_next_lst'         :refl_for_next_lst,
                'another_for_next_lst'      :another_for_next_lst,
                'tuning_params_lst'         :tuning_params_lst,
                'parent_pos_lst'            :[],
                'chidren_pos_lst'           :[],
                'files_from_parent_dict'    :{},
                'external_files_lst'        :[],
                'log_file_path'             :log_file_path,
                'curr_poss'                 :curr_poss,
            }
            curr_poss += 1
            list_of_commands.append(cmd_dict)

    find_work_dirs(list_of_commands)

    # node number (curr_poss) -> command dict, numbers start at bigger_lin_in
    cmd_by_poss = {}
    for cmd_dict in list_of_commands:
        cmd_by_poss[cmd_dict['curr_poss']] = cmd_dict

    # file name -> position of the most recent command that wrote it
    producer_of = {}
    for cur_num, curr_dict in enumerate(list_of_commands):
        for file_name in (
            curr_dict['expt_from_prev_lst'] +
            curr_dict['refl_from_prev_lst'] +
            curr_dict['another_from_prev_lst']
        ):
            if file_name in producer_of:
                parent_poss = producer_of[file_name]

            else:
                parent_poss = find_implicit_parent(
                    file_name, list_of_commands[0:cur_num]
                )

            if parent_poss is None:
                curr_dict['external_files_lst'].append(file_name)
                continue

            add_connection(cmd_by_poss[parent_poss], curr_dict, file_name)

        for single_par in curr_dict['tuning_params_lst']:
            parent_poss = find_param_parent(
                single_par, list_of_commands[0:cur_num]
            )
            if parent_poss is not None:
                add_connection(
                    cmd_by_poss[parent_poss], curr_dict, single_par
                )

        for file_name in (
            curr_dict['expt_for_next_lst'] +
            curr_dict['refl_for_next_lst'] +
            curr_dict['another_for_next_lst']
        ):
            producer_of[file_name] = curr_dict['curr_poss']

    return list_of_commands, curr_poss


def run_predicts_n_reports(all_dat, arg_in):

    lst_cmd = all_dat['step_list']

    print("\n lst_cmd =", lst_cmd, "\n")

    cur_wor_dir = os.getcwd()
    print("\n os.getcwd() =", cur_wor_dir)
    '''
    path_to_dir = arg_in[0:-15] + os.sep + "predicts_n_reports"
    print("path_to_dir =", path_to_dir)
    '''
    path_to_dir = "predicts_n_reports"

    print("path_to_dir =", path_to_dir)

    #os.mkdir(path_to_dir)

    try:
        os.mkdir(path_to_dir)

    except FileExistsError:
        print("NOT creating again: ", path_to_dir)


    os.chdir(path_to_dir)

    new_cur_wor_dir = os.getcwd()
    print("NEW path_to_dir =", new_cur_wor_dir)



    for cmd_node in lst_cmd:
        rep_lst_dat_in = ['dials.report']
        for expt_2_add in cmd_node['_lst_expt_out']:
            rep_lst_dat_in.append(expt_2_add)

        for refl_2_add in cmd_node['_lst_refl_out']:
            rep_lst_dat_in.append(refl_2_add)

        number_str = str(cmd_node['number'])
        output_html_file = "dials.report_n_"+ number_str +".html"
        rep_lst_dat_in.append("output.html=" + output_html_file)

        print("\n running:" + str(rep_lst_dat_in))

        new_line = "Generating HTML report"
        print(new_line)

        lst_rep_out = []
        rep_lst_dat_in[0] = str(shutil.which(rep_lst_dat_in[0]))
        rep_proc = subprocess.Popen(
            rep_lst_dat_in,
            shell = False,
            cwd = new_cur_wor_dir,
            stdout = subprocess.PIPE,
            stderr = subprocess.STDOUT,
            universal_newlines = True
        )
        while rep_proc.poll() is None or new_line != '':
            new_line = rep_proc.stdout.readline()
            print(new_line)
            lst_rep_out.append(new_line)

        #TODO: consider if we need to capture the output for this command

        rep_proc.stdout.close()
        # in case needed there is the output of the report here:
        #logging.info("report stdout <<< \n", lst_rep_out, "\n >>>")

        tmp_html_path = new_cur_wor_dir + os.sep + output_html_file

        if os.path.exists(tmp_html_path):
            lst_rep_out.append("HTML Report ready")
            cmd_node['_html_rep'] = str(tmp_html_path)

        else:
            lst_rep_out.append("No need for HTML report in this step")

        for line_2_print in lst_rep_out:
            print(">>> ", line_2_print[:-1])

    os.chdir(cur_wor_dir)

    all_dat['step_list'] = lst_cmd
    print("\n done \n")

    for num, cmd in enumerate(lst_cmd):
        print(
            num, " node(number):", cmd['number'], " lst2run:", cmd['lst2run'],
            " child_node_lst:", cmd['child_node_lst'],
            " parent_node_lst:", cmd['parent_node_lst'],
        )

    return all_dat


def get_tree(path_to_xia2_run_dir, bigger_lin_from_dui2):
    print("path_to_xia2_run_dir =", path_to_xia2_run_dir)
    ended_str = os.sep + "xia2-debug.txt"
    if path_to_xia2_run_dir.endswith(ended_str):
        print("using path: \'", path_to_xia2_run_dir, "\' as given by user")
        path_to_debug_file = path_to_xia2_run_dir

    else:
        print("adding: \'", ended_str, "\' to given path")
        path_to_debug_file = path_to_xia2_run_dir + ended_str

    lst_cmd, bigger_lin_out = get_list_of_commands(
        path_in = path_to_debug_file,
        bigger_lin_in = bigger_lin_from_dui2 + 1
    )
    all_dat = build_reusable_graph_list(lst_cmd)
    all_dat = run_predicts_n_reports(all_dat, ended_str)

    return all_dat


def main():
    try:
        arg_in = sys.argv[1]

    except IndexError:
        print("\n Enter path of file to read /FULL/PATH/TO/xia2-debug.txt \n")
        all_dat = {}

    else:
        lst_cmd, bigger_lin_out = get_list_of_commands(
            path_in = arg_in, bigger_lin_in = 0
        )
        all_dat = build_reusable_graph_list(lst_cmd)
        all_dat = run_predicts_n_reports(all_dat, arg_in)
        print_graph_table(list_of_commands = lst_cmd)

    export_reusable_graph_list(all_dat)


if __name__ == "__main__":
    main()
