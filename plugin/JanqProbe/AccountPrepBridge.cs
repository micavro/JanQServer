using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using Janq;
using MJM;
using MJM.Dialogue;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace JanqProbe;

internal sealed class AccountPrepRequest
{
    public string? id { get; set; }

    public string? nickname { get; set; }

    public int? maxStories { get; set; }
}

internal sealed class AccountPrepStatus
{
    public int version { get; set; } = 1;

    public string? requestId { get; set; }

    public string? stage { get; set; }

    public bool active { get; set; }

    public string? nickname { get; set; }

    public string? scene { get; set; }

    public string? sequence { get; set; }

    public bool accountCaptured { get; set; }

    public int? initialMjchip { get; set; }

    public int? currentMjchip { get; set; }

    public List<int> completedStories { get; set; } = new();

    public int? maxStories { get; set; }

    public List<int> exhaustedChapters { get; set; } = new();

    public int? currentChapterId { get; set; }

    public List<int> inaccessibleChapters { get; set; } = new();

    public string? error { get; set; }

    public string? screenshotPath { get; set; }

    public string? updatedAt { get; set; }
}

internal sealed class StoredAccount
{
    public string? requestId { get; set; }

    public string? createdAt { get; set; }

    public string? loginId { get; set; }

    public string? password { get; set; }

    public string? nickname { get; set; }

    public int? finalMjchip { get; set; }

    public string? status { get; set; }
}

internal sealed class AccountStore
{
    public int version { get; set; } = 1;

    public List<StoredAccount> accounts { get; set; } = new();
}

internal static class AccountPrepBridge
{
    private static readonly object Sync = new();
    private static readonly HashSet<int> CompletedStories = new();
    private static readonly HashSet<int> ExhaustedChapters = new();
    private static readonly List<int> InaccessibleChapters = new();
    private static string rootPath = "";
    private static string requestPath = "";
    private static string statusPath = "";
    private static string accountsPath = "";
    private static AccountPrepRequest? request;
    private static bool active;
    private static bool accountCaptured;
    private static bool storyModeStarted;
    private static bool logoutRequested;
    private static bool accountSwitchRequested;
    private static bool casinoReturnRequested;
    private static bool skipIssued;
    private static bool yakuhimeDownloadConfirmed;
    private static int currentChapterId;
    private static int currentChapterIndex;
    private static int lastStoryId;
    private static int consecutiveErrors;
    private static float nextTickAt;
    private static float nextStatusAt;
    private static float nextScreenshotAt;
    private static float nextProgressScreenshotAt;
    private static float storySceneReadyAt;
    private static float lastStoryRequestedAt;
    private static float mainMenuReadyAt;
    private static float yakuhimeSubMenuReadyAt;
    private static string stage = "idle";
    private static string? lastError;
    private static string? lastScreenshotPath;
    private static int? initialMjchip;
    private static int? currentMjchip;

    public static bool IsActive => active;

    public static void Initialize(string gameRoot)
    {
        var workspace = Path.GetFullPath(Path.Combine(gameRoot, "..", ".."));
        rootPath = Path.Combine(workspace, "_runtime", "account_prep");
        requestPath = Path.Combine(rootPath, "request.json");
        statusPath = Path.Combine(rootPath, "status.json");
        accountsPath = Path.Combine(workspace, "_runtime", "accounts", "accounts.json");
        Directory.CreateDirectory(rootPath);
        Directory.CreateDirectory(Path.GetDirectoryName(accountsPath)!);
        ProbeLog.Write("account_prep_bridge_ready", new { rootPath });
    }

    public static void Shutdown()
    {
        lock (Sync)
        {
            if (active)
            {
                WriteStatus(force: true);
            }
        }
    }

    public static bool ShouldBlockBrowser(string? url)
    {
        return active && url?.IndexOf("/info/shoukai.html", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    public static void Tick()
    {
        if (string.IsNullOrEmpty(rootPath) || Time.realtimeSinceStartup < nextTickAt)
        {
            return;
        }
        nextTickAt = Time.realtimeSinceStartup + 0.25f;

        lock (Sync)
        {
            if (!active)
            {
                TryStartRequest();
                return;
            }

            try
            {
                Process();
                consecutiveErrors = 0;
            }
            catch (Exception ex)
            {
                consecutiveErrors += 1;
                lastError = ex.ToString();
                ProbeLog.Write("account_prep_tick_failed", new
                {
                    stage,
                    consecutiveErrors,
                    error = ex.ToString()
                });
                CaptureDiagnosticScreenshot("tick_failed");
                if (consecutiveErrors >= 8)
                {
                    Fail("repeated_controller_error", ex.ToString());
                }
            }

            WriteStatus(force: false);
        }
    }

    private static void TryStartRequest()
    {
        if (!File.Exists(requestPath))
        {
            return;
        }

        var candidate = JsonConvert.DeserializeObject<AccountPrepRequest>(File.ReadAllText(requestPath));
        if (candidate == null || string.IsNullOrWhiteSpace(candidate.id))
        {
            return;
        }

        request = candidate;
        request.nickname = NormalizeNickname(request.nickname, request.id!);
        RestoreCheckpoint();
        active = true;
        stage = accountCaptured ? "resume_after_account_capture" : "starting";
        lastError = null;
        ProbeLog.Write("account_prep_started", new
        {
            requestId = request.id,
            nickname = request.nickname,
            resumed = accountCaptured
        });
        WriteStatus(force: true);
    }

    private static void RestoreCheckpoint()
    {
        CompletedStories.Clear();
        ExhaustedChapters.Clear();
        InaccessibleChapters.Clear();
        accountCaptured = false;
        storyModeStarted = false;
        logoutRequested = false;
        accountSwitchRequested = false;
        casinoReturnRequested = false;
        skipIssued = false;
        yakuhimeDownloadConfirmed = false;
        currentChapterId = 0;
        currentChapterIndex = 0;
        lastStoryId = 0;
        mainMenuReadyAt = 0f;
        yakuhimeSubMenuReadyAt = 0f;
        nextProgressScreenshotAt = 0f;
        lastScreenshotPath = null;
        initialMjchip = null;
        currentMjchip = null;

        if (!File.Exists(statusPath))
        {
            return;
        }

        try
        {
            var checkpoint = JsonConvert.DeserializeObject<AccountPrepStatus>(File.ReadAllText(statusPath));
            if (checkpoint == null || checkpoint.requestId != request?.id || !checkpoint.active)
            {
                return;
            }
            accountCaptured = checkpoint.accountCaptured;
            initialMjchip = checkpoint.initialMjchip;
            currentMjchip = checkpoint.currentMjchip;
            foreach (var storyId in checkpoint.completedStories)
            {
                CompletedStories.Add(storyId);
            }
            foreach (var chapterId in checkpoint.exhaustedChapters)
            {
                ExhaustedChapters.Add(chapterId);
            }
            InaccessibleChapters.AddRange(checkpoint.inaccessibleChapters);
            currentChapterId = checkpoint.currentChapterId ?? 0;
        }
        catch (Exception ex)
        {
            ProbeLog.Write("account_prep_checkpoint_ignored", new { error = ex.Message });
        }
    }

    private static void Process()
    {
        var observedMjchip = ReadMjchip();
        if (observedMjchip.HasValue)
        {
            currentMjchip = observedMjchip;
            if (accountCaptured && !initialMjchip.HasValue && CompletedStories.Count == 0)
            {
                initialMjchip = observedMjchip;
                UpdateStoredInitialMjchip(observedMjchip.Value);
                ProbeLog.Write("account_prep_initial_mjchip_captured", new
                {
                    requestId = request?.id,
                    initialMjchip
                });
            }
        }
        var scene = SceneManager.GetActiveScene().name;
        CaptureProgressScreenshot(scene);
        if (!accountCaptured)
        {
            ProcessAccountCreation(scene);
            return;
        }

        ProcessStoryMining(scene);
    }

    private static void ProcessAccountCreation(string scene)
    {
        var gameManager = UnityEngine.Object.FindObjectOfType<GameManager>();
        if (gameManager != null)
        {
            var state = CurrentGameState(gameManager);
            var requestState = CurrentGameRequestState(gameManager);
            var exit = FieldText(gameManager, "mExitButtonType");
            var exitRequest = FieldText(gameManager, "mExitButtonRequest");
            if ((state == "BetWait" || requestState == "BetWait")
                && (exit == "Exit" || exitRequest == "Exit"))
            {
                SetStage("leaving_janq_at_safe_point");
                gameManager.ExitButtonClick();
            }
            else
            {
                SetStage("waiting_for_safe_janq_exit");
            }
            return;
        }

        if (scene == "BlackJack")
        {
            if (!casinoReturnRequested)
            {
                MenuSequenceManager.LastAutholizedGame = MenuSequenceManager.LastPlayAutholizedGameType.CASINO;
                MenuSequenceManager.DestinationFromCasinoScene = MenuSequenceManager.Destination.MAIN_MENU;
                SceneManager.LoadScene("Menu");
                casinoReturnRequested = true;
                SetStage("returning_casino_to_menu");
            }
            return;
        }

        if (scene == "Menu")
        {
            casinoReturnRequested = false;
            var manager = FindManager("MenuSequenceManager");
            if (manager == null)
            {
                SetStage("waiting_menu_manager_for_logout");
                return;
            }
            if (!logoutRequested)
            {
                var logout = CreateInstance("Menu.LogoutSequence", manager);
                if (ForceNext(manager, logout))
                {
                    logoutRequested = true;
                    SetStage("logout_requested");
                }
            }
            return;
        }

        if (scene == "Login")
        {
            ProcessLoginScene();
            return;
        }

        if (scene == "Regist")
        {
            ProcessRegistrationScene();
            return;
        }

        SetStage("waiting_account_scene_" + scene);
        CaptureDiagnosticScreenshot("unknown_account_scene");
    }

    private static void ProcessLoginScene()
    {
        var manager = FindManager("LoginSequenceManager");
        var sequence = CurrentInnerSequence(manager);
        var name = sequence?.GetType().FullName;
        if (sequence == null || manager == null)
        {
            SetStage("waiting_login_sequence");
            return;
        }

        if (name?.Contains("SafetyLogoutDialogSequence") == true)
        {
            SetField(sequence, "isFinish", true);
            SetStage("login_safety_logout_confirmed");
            return;
        }

        switch (name)
        {
            case "Login.SegaLogoSequence":
                AccessTools.Method(sequence.GetType(), "onTouch")?.Invoke(sequence, new object?[] { null, null });
                SetStage("advancing_login_logo");
                return;
            case "Login.LoginButtonSequence":
            {
                if (accountSwitchRequested)
                {
                    return;
                }
                var accountData = FieldValue(sequence, "accountData") ?? LoadAccountData();
                var next = CreateSequence("Login.AccountSwitchingSequence", accountData!, manager, true);
                SetField(sequence, "next", next);
                accountSwitchRequested = true;
                SetStage("account_switch_requested");
                return;
            }
            case "Login.AccountSwitchingSequence":
            {
                var field = AccessTools.Field(sequence.GetType(), "currentSequence")
                    ?? throw new MissingFieldException(name, "currentSequence");
                field.SetValue(sequence, Enum.Parse(field.FieldType, "Regist"));
                SetStage("new_registration_selected");
                return;
            }
            case "Login.LoginErrorSequence":
                Fail("login_error_during_account_prep", name);
                return;
            default:
                SetStage("waiting_login_" + SafeName(name));
                return;
        }
    }

    private static void ProcessRegistrationScene()
    {
        var manager = FindManager("RegistSequenceManager");
        var sequence = CurrentInnerSequence(manager);
        var name = sequence?.GetType().FullName;
        if (sequence == null || manager == null)
        {
            SetStage("waiting_registration_sequence");
            return;
        }

        // Account switching can insert the same safety-logout confirmation that
        // the normal UI advances by setting this sequence's completion flag.
        // Keep this handling local to account preparation so the JanQ navigator
        // and its gameplay flow remain untouched.
        if (name?.Contains("SafetyLogoutDialogSequence") == true)
        {
            SetField(sequence, "isFinish", true);
            SetStage("registration_safety_logout_confirmed");
            return;
        }

        switch (name)
        {
            case "Regist.SelectCharacterTypeSequence":
            {
                var data = RequiredField(sequence, "registData");
                var avatars = FieldValue(data, "selectableAvatarArray") as IList;
                if (avatars == null || avatars.Count == 0)
                {
                    SetStage("waiting_registration_avatars");
                    return;
                }
                var index = Math.Abs((request?.id ?? "janq").GetHashCode()) % avatars.Count;
                SetMember(data, "selectIndex", index);
                SetField(sequence, "next", CreateSequence("Regist.InputNicknameSequence", data, manager));
                SetStage("registration_avatar_selected");
                return;
            }
            case "Regist.InputNicknameSequence":
            {
                var data = RequiredField(sequence, "registData");
                SetMember(data, "Nickname", request!.nickname!);
                SetField(sequence, "next", CreateSequence("Regist.SelectRegionGroupSequence", data, manager));
                SetStage("registration_nickname_set");
                return;
            }
            case "Regist.SelectRegionGroupSequence":
            {
                var data = RequiredField(sequence, "registData");
                SetEnumMember(data, "MyRegionGroup", "Etc");
                SetEnumMember(data, "MyRegion", "Etc");
                SetField(sequence, "next", CreateSequence("Regist.ConfirmRegistDataSequence", data, manager));
                SetStage("registration_region_set");
                return;
            }
            case "Regist.SelectRegionSequence":
            {
                var data = RequiredField(sequence, "registData");
                SetEnumMember(data, "MyRegion", "Etc");
                SetField(sequence, "next", CreateSequence("Regist.ConfirmRegistDataSequence", data, manager));
                SetStage("registration_region_set");
                return;
            }
            case "Regist.ConfirmRegistDataSequence":
            {
                var data = RequiredField(sequence, "registData");
                SetField(sequence, "next", CreateSequence("Regist.ConfirmAgreementSequence", data, manager));
                SetStage("registration_profile_confirmed");
                return;
            }
            case "Regist.ConfirmAgreementSequence":
            {
                var data = RequiredField(sequence, "registData");
                SetField(sequence, "next", CreateSequence("Regist.RequestRegistSequence", data, manager));
                SetStage("registration_agreement_accepted");
                return;
            }
            case "Regist.DispMJMIDSequence":
            {
                var data = RequiredField(sequence, "registData");
                CaptureAccount(data);
                SetField(sequence, "isFinish", true);
                SetStage("account_registered_logging_in");
                return;
            }
            case "Regist.LoginErrorSequence":
            case "Regist.LoginErrorAndConfirmDeleteAccountSequence":
                Fail("registration_login_error", name);
                return;
            default:
                SetStage("waiting_registration_" + SafeName(name));
                return;
        }
    }

    private static void ProcessStoryMining(string scene)
    {
        if (scene == "Login")
        {
            ProcessCapturedAccountLogin();
            return;
        }

        if (scene == "Regist")
        {
            var registManager = FindManager("RegistSequenceManager");
            var registSequence = CurrentInnerSequence(registManager);
            var registName = registSequence?.GetType().FullName;
            if (registName == "Regist.FirstDownloadOnlineResourcesSequence")
            {
                SetField(registSequence!, "isLoaded", true);
                SetStage("finishing_first_resource_sync");
                return;
            }
            if (registName?.Contains("SafetyLogoutDialogSequence") == true)
            {
                SetField(registSequence!, "isFinish", true);
                SetStage("post_registration_safety_logout_confirmed");
                return;
            }
            SetStage("waiting_post_registration_" + SafeName(registName ?? "Regist"));
            return;
        }

        if (scene == "YakuhimeStory")
        {
            ProcessStoryScene();
            return;
        }

        if (scene != "Menu")
        {
            SetStage("waiting_post_registration_menu_" + scene);
            return;
        }

        var manager = FindManager("MenuSequenceManager");
        var sequence = CurrentInnerSequence(manager);
        var sequenceName = sequence?.GetType().FullName;
        if (manager == null)
        {
            SetStage("waiting_menu_manager_for_yakuhime");
            return;
        }

        if (sequenceName?.Contains("SafetyLogoutDialogSequence") == true)
        {
            lastError = FieldValue(sequence!, "message")?.ToString();
            SetField(sequence!, "isFinish", true);
            SetStage("menu_safety_logout_confirmed");
            return;
        }

        if (sequenceName?.Contains("MessageDialogSequence") == true)
        {
            SetField(sequence!, "isFinish", true);
            SetStage("dismissing_post_login_menu_dialog");
            return;
        }

        // A server-requested safety logout returns through Login and creates a
        // fresh MainMenu sequence. Re-arm the Yakuhime jump after that recovery.
        if (sequenceName == "Menu.MainMenu")
        {
            storyModeStarted = false;
            if (mainMenuReadyAt <= 0f)
            {
                mainMenuReadyAt = Time.realtimeSinceStartup + 5f;
            }
            if (Time.realtimeSinceStartup < mainMenuReadyAt)
            {
                SetStage("settling_main_menu_before_yakuhime");
                return;
            }
        }

        if (sequenceName == "MJM.Yakuhime.YakuhimeQuestListMenu")
        {
            storyModeStarted = true;
            ProcessQuestList(sequence!);
            return;
        }

        if (sequenceName == "MJM.Yakuhime.YakuhimeStoryMenu")
        {
            storyModeStarted = true;
            ProcessStoryMenu(sequence!);
            return;
        }

        if (sequenceName == "Menu.SubMenu_Yakuhime")
        {
            if (!yakuhimeDownloadConfirmed && TryConfirmYakuhimeDownload())
            {
                yakuhimeDownloadConfirmed = true;
                SetStage("confirming_yakuhime_resource_download");
                return;
            }
            var touchChara = FieldValue(sequence!, "touchChara");
            var container = touchChara == null ? null : FieldValue(touchChara, "_container");
            var charaLoaded = container != null && Convert.ToBoolean(GetMember(container, "IsSpineCharaLoaded"));
            if (!charaLoaded)
            {
                if (yakuhimeSubMenuReadyAt <= 0f)
                {
                    yakuhimeSubMenuReadyAt = Time.realtimeSinceStartup + 18f;
                    SetStage("loading_yakuhime_submenu_resources");
                    return;
                }
                if (Time.realtimeSinceStartup < yakuhimeSubMenuReadyAt)
                {
                    SetStage("loading_yakuhime_submenu_resources");
                    return;
                }
                ProbeLog.Write("account_prep_yakuhime_chara_load_timeout_continue", new
                {
                    stage,
                    scene,
                    sequence = sequenceName,
                    hasTouchChara = touchChara != null,
                    hasContainer = container != null,
                    containerType = container?.GetType().FullName
                });
            }
            else if (yakuhimeSubMenuReadyAt <= 0f)
            {
                yakuhimeSubMenuReadyAt = Time.realtimeSinceStartup + 3f;
            }
            if (Time.realtimeSinceStartup < yakuhimeSubMenuReadyAt)
            {
                SetStage("settling_yakuhime_submenu");
                return;
            }
            var questList = CreateYakuhimeQuestList(manager, playEnterVoice: false, chapterIndex: 1);
            if (ForceNext(manager, questList))
            {
                SetStage("yakuhime_quest_list_requested");
            }
            return;
        }

        if (!storyModeStarted)
        {
            SetStaticProperty("MenuSequenceManager", "IsFinishBootDemo", true);
            SetField(manager, "IsPassedMainMenu", true);
            var submenu = CreateSequence("Menu.SubMenu_Yakuhime", manager, false, false);
            if (ForceNext(manager, submenu))
            {
                storyModeStarted = true;
                SetStage("yakuhime_submenu_requested");
            }
            else
            {
                SetStage("waiting_to_enter_yakuhime");
            }
            return;
        }

        SetStage("waiting_yakuhime_sequence_" + SafeName(sequenceName));
    }

    private static void ProcessCapturedAccountLogin()
    {
        mainMenuReadyAt = 0f;
        yakuhimeSubMenuReadyAt = 0f;
        yakuhimeDownloadConfirmed = false;
        var manager = FindManager("LoginSequenceManager");
        var sequence = CurrentInnerSequence(manager);
        var name = sequence?.GetType().FullName;
        if (sequence == null)
        {
            SetStage("waiting_captured_account_login");
            return;
        }

        if (name?.Contains("SafetyLogoutDialogSequence") == true)
        {
            SetField(sequence, "isFinish", true);
            SetStage("captured_account_safety_logout_confirmed");
            return;
        }
        if (name == "Login.SegaLogoSequence")
        {
            AccessTools.Method(sequence.GetType(), "onTouch")?.Invoke(sequence, new object?[] { null, null });
            SetStage("advancing_captured_account_login_logo");
            return;
        }
        if (name == "Login.LoginButtonSequence")
        {
            var button = FieldValue(sequence, "login_button");
            var onPushAnimEnded = button == null ? null : FieldValue(button, "OnPushAnimEnded");
            var exec = onPushAnimEnded == null ? null : AccessTools.Method(onPushAnimEnded.GetType(), "Exec");
            if (exec == null)
            {
                SetStage("waiting_captured_account_login_button");
                return;
            }
            exec.Invoke(onPushAnimEnded, new[] { sequence });
            SetStage("captured_account_login_requested");
            return;
        }
        if (name == "Login.DownloadOnlineResourceSequence")
        {
            // Authentication and profile loading have already succeeded. This
            // sequence's normal next destination is Menu; some first-login
            // decorator stacks fail to advance while running minimized.
            SceneManager.LoadScene("Menu");
            SetStage("captured_account_menu_requested");
            return;
        }
        if (name == "Login.LoginErrorSequence")
        {
            Fail("captured_account_login_error", name);
            return;
        }
        SetStage("waiting_captured_account_login_" + SafeName(name ?? "sequence"));
    }

    private static bool TryConfirmYakuhimeDownload()
    {
        return TryConfirmDialogButton("StartDownload", "account_prep_yakuhime_download_confirmed");
    }

    private static bool TryConfirmDialogButton(string requestedButton, string eventType)
    {
        var dialogType = RequiredType("MJM.SystemUI.MessageDialog");
        var instances = AccessTools.Field(dialogType, "instances")?.GetValue(null) as IList;
        if (instances == null)
        {
            return false;
        }
        for (var dialogIndex = instances.Count - 1; dialogIndex >= 0; dialogIndex -= 1)
        {
            var dialog = instances[dialogIndex];
            if (dialog == null || Convert.ToBoolean(GetMember(dialog, "disposed")))
            {
                continue;
            }
            var buttons = FieldValue(dialog, "buttons") as IList;
            if (buttons == null)
            {
                continue;
            }
            foreach (var button in buttons.Cast<object>())
            {
                var buttonType = GetMember(button, "Type");
                if (buttonType?.ToString() != requestedButton)
                {
                    continue;
                }
                var onPush = FieldValue(dialog, "OnPush");
                var exec = onPush == null ? null : AccessTools.Method(onPush.GetType(), "Exec");
                if (exec == null)
                {
                    return false;
                }
                exec.Invoke(onPush, new[] { buttonType });
                ProbeLog.Write(eventType, new { button = requestedButton });
                return true;
            }
        }
        return false;
    }

    private static void ProcessQuestList(object sequence)
    {
        if (ReachedStoryLimit())
        {
            Complete();
            return;
        }

        var window = FieldValue(sequence, "yakuhimeQuestListWindow");
        var chapters = window == null ? null : FieldValue(window, "chapterList") as IList;
        if (chapters == null)
        {
            SetStage("loading_yakuhime_chapters");
            return;
        }

        InaccessibleChapters.Clear();
        var candidates = new List<object>();
        foreach (var chapter in chapters.Cast<object>())
        {
            var chapterId = InvokeInt(chapter, "GetChapterId");
            var open = InvokeBool(chapter, "IsOpen");
            var clear = InvokeBool(chapter, "IsClear");
            if (!open && !clear)
            {
                InaccessibleChapters.Add(chapterId);
            }
            if (open && !clear && !ExhaustedChapters.Contains(chapterId))
            {
                candidates.Add(chapter);
            }
        }

        if (candidates.Count == 0)
        {
            Complete();
            return;
        }

        var selected = candidates
            .OrderBy(item => InvokeInt(item, "GetChapterOrder"))
            .ThenBy(item => InvokeInt(item, "GetChapterId"))
            .First();
        currentChapterId = InvokeInt(selected, "GetChapterId");
        currentChapterIndex = Convert.ToInt32(Invoke(selected, "GetChapterIndex"));
        InvokeStatic("MJM.Yakuhime.YakuhimeQuestListMenu", "RequestYakuhimeStoryWindow", selected);
        SetStage("opening_yakuhime_chapter_" + currentChapterId);
    }

    private static void ProcessStoryMenu(object sequence)
    {
        if (TryConfirmDialogButton("OK", "account_prep_story_dialog_confirmed"))
        {
            SetStage("dismissing_yakuhime_story_dialog");
            return;
        }

        var window = FieldValue(sequence, "yakuhimeStoryWindow");
        var stories = window == null ? null : FieldValue(window, "infoList") as IList;
        if (stories == null)
        {
            SetStage(lastStoryId == 0 ? "loading_yakuhime_story_list" : "settling_yakuhime_reward");
            return;
        }

        if (lastStoryId != 0)
        {
            var completed = stories.Cast<object>()
                .FirstOrDefault(item => InvokeInt(item, "GetStoryId") == lastStoryId);
            if (completed != null && InvokeBool(completed, "IsClear"))
            {
                Invoke(sequence, "ApplyQuestEndMjChipToFundageDisp");
                currentMjchip = ReadMjchip() ?? currentMjchip;
                CompletedStories.Add(lastStoryId);
                ProbeLog.Write("account_prep_story_completed", new
                {
                    storyId = lastStoryId,
                    chapterId = currentChapterId,
                    mjchip = ReadMjchip()
                });
                lastStoryId = 0;
                skipIssued = false;
                if (ReachedStoryLimit())
                {
                    Complete();
                    return;
                }
            }
            else
            {
                if (Time.realtimeSinceStartup - lastStoryRequestedAt > 90f)
                {
                    ProbeLog.Write("account_prep_story_reward_timeout_skip", new
                    {
                        storyId = lastStoryId,
                        chapterId = currentChapterId,
                        elapsed = Time.realtimeSinceStartup - lastStoryRequestedAt
                    });
                    ExhaustedChapters.Add(currentChapterId);
                    lastStoryId = 0;
                    skipIssued = false;
                    var timeoutParent = FieldValue(sequence, "parent") ?? FindManager("MenuSequenceManager")!;
                    var timeoutQuestList = CreateYakuhimeQuestList(timeoutParent, playEnterVoice: false, currentChapterIndex == 0 ? 1 : currentChapterIndex);
                    SetField(sequence, "next", timeoutQuestList);
                    SetStage("skipping_unconfirmed_yakuhime_chapter_" + currentChapterId);
                }
                else
                {
                    SetStage("confirming_yakuhime_story_" + lastStoryId);
                }
                return;
            }
        }

        var pending = stories.Cast<object>()
            .Where(item => InvokeBool(item, "IsPlayable"))
            .Where(item => !InvokeBool(item, "IsVsCpu"))
            .Where(item => !InvokeBool(item, "IsClear"))
            .OrderBy(item => InvokeInt(item, "GetStoryIndex"))
            .FirstOrDefault();

        if (pending != null)
        {
            var storyId = InvokeInt(pending, "GetStoryId");
            var storyIndex = InvokeInt(pending, "GetStoryIndex");
            var eventId = Convert.ToInt32(FieldValue(window!, "event_id") ?? 0);
            var han = Convert.ToInt32(FieldValue(window!, "han") ?? 1);
            var himeId = Convert.ToInt32(FieldValue(window!, "hime_id") ?? -1);
            var script = InvokeStatic(
                "MJM.Yakuhime.YakuhimeStoryUtil",
                "GetYakuhimeScriptName",
                eventId,
                han,
                himeId,
                storyIndex
            )?.ToString();
            if (string.IsNullOrWhiteSpace(script))
            {
                throw new InvalidOperationException($"story_script_missing:{storyId}");
            }

            InvokeStatic(
                "MJM.Yakuhime.YakuhimeStoryMenu",
                "RequestYakuhimeStoryStart",
                script!,
                storyId,
                false
            );
            lastStoryId = storyId;
            lastStoryRequestedAt = Time.realtimeSinceStartup;
            storySceneReadyAt = 0f;
            skipIssued = false;
            SetStage("starting_yakuhime_story_" + storyId);
            ProbeLog.Write("account_prep_story_requested", new
            {
                storyId,
                storyIndex,
                chapterId = currentChapterId
            });
            return;
        }

        ExhaustedChapters.Add(currentChapterId);
        var parent = FieldValue(sequence, "parent") ?? FindManager("MenuSequenceManager")!;
        var questList = CreateYakuhimeQuestList(parent, playEnterVoice: false, currentChapterIndex == 0 ? 1 : currentChapterIndex);
        SetField(sequence, "next", questList);
        SetStage("returning_from_yakuhime_chapter_" + currentChapterId);
    }

    private static void ProcessStoryScene()
    {
        var playback = UnityEngine.Object.FindObjectOfType<PlaybackEngine>();
        if (playback == null || playback.model == null || playback.model.isLoading)
        {
            if (lastStoryRequestedAt > 0f && Time.realtimeSinceStartup - lastStoryRequestedAt > 75f)
            {
                ProbeLog.Write("account_prep_story_load_timeout_returning_menu", new
                {
                    storyId = lastStoryId,
                    chapterId = currentChapterId,
                    elapsed = Time.realtimeSinceStartup - lastStoryRequestedAt,
                    hasPlayback = playback != null,
                    hasModel = playback?.model != null,
                    modelLoading = playback?.model?.isLoading
                });
                SceneManager.LoadScene("Menu");
                SetStage("story_load_timeout_returning_menu_" + lastStoryId);
                return;
            }
            SetStage("loading_yakuhime_story_" + lastStoryId);
            return;
        }

        if (storySceneReadyAt <= 0f)
        {
            storySceneReadyAt = Time.realtimeSinceStartup + 3f;
            SetStage("playing_yakuhime_story_" + lastStoryId);
            return;
        }

        if (!skipIssued && Time.realtimeSinceStartup >= storySceneReadyAt)
        {
            playback.ToEnd();
            skipIssued = true;
            SetStage("skipping_yakuhime_story_" + lastStoryId);
        }
    }

    private static void CaptureAccount(object data)
    {
        if (accountCaptured)
        {
            return;
        }

        var loginId = GetMember(data, "ID")?.ToString();
        var password = GetMember(data, "Password")?.ToString();
        if (string.IsNullOrWhiteSpace(loginId) || string.IsNullOrWhiteSpace(password))
        {
            throw new InvalidOperationException("generated_credentials_missing");
        }

        initialMjchip = ReadMjchip();
        var store = LoadAccountStore();
        var existing = store.accounts.FirstOrDefault(item => item.requestId == request?.id);
        if (existing == null)
        {
            existing = new StoredAccount { requestId = request?.id };
            store.accounts.Add(existing);
        }
        existing.createdAt = DateTimeOffset.UtcNow.ToString("O");
        existing.loginId = loginId;
        existing.password = password;
        existing.nickname = request?.nickname;
        existing.status = "registered";
        SaveAccountStore(store);
        accountCaptured = true;
        ProbeLog.Write("account_prep_account_registered", new
        {
            requestId = request?.id,
            nickname = request?.nickname,
            initialMjchip
        });
        WriteStatus(force: true);
    }

    private static void Complete()
    {
        currentMjchip = ReadMjchip() ?? currentMjchip;
        UpdateStoredAccount("complete", currentMjchip);
        stage = InaccessibleChapters.Count == 0
            ? "complete"
            : "complete_accessible_stories";
        active = false;
        lastError = null;
        WriteStatus(force: true);
        ProbeLog.Write("account_prep_completed", new
        {
            requestId = request?.id,
            completedStories = CompletedStories.OrderBy(id => id).ToArray(),
            maxStories = request?.maxStories,
            exhaustedChapters = ExhaustedChapters.OrderBy(id => id).ToArray(),
            inaccessibleChapters = InaccessibleChapters.OrderBy(id => id).ToArray(),
            mjchip = currentMjchip
        });
        ArchiveRequest("done");
    }

    private static bool ReachedStoryLimit()
    {
        return request?.maxStories is > 0 && CompletedStories.Count >= request.maxStories.Value;
    }

    private static void Fail(string reason, string? detail)
    {
        lastError = detail == null ? reason : reason + ":" + detail;
        stage = "failed";
        active = false;
        UpdateStoredAccount("failed", ReadMjchip());
        WriteStatus(force: true);
        ProbeLog.Write("account_prep_failed", new { reason, detail });
        CaptureDiagnosticScreenshot("failed");
        ArchiveRequest("failed");
    }

    private static void UpdateStoredAccount(string status, int? finalMjchip)
    {
        if (!accountCaptured)
        {
            return;
        }
        var store = LoadAccountStore();
        var existing = store.accounts.FirstOrDefault(item => item.requestId == request?.id);
        if (existing == null)
        {
            return;
        }
        existing.status = status;
        existing.finalMjchip = finalMjchip;
        SaveAccountStore(store);
    }

    private static void UpdateStoredInitialMjchip(int value)
    {
        // The compact accounts.json intentionally stores only final balance.
        // Initial MJ remains available in account-prep status/logs while a run is active.
    }

    private static AccountStore LoadAccountStore()
    {
        try
        {
            if (!File.Exists(accountsPath))
            {
                return new AccountStore();
            }

            var token = JToken.Parse(File.ReadAllText(accountsPath));
            if (token.Type == JTokenType.Array)
            {
                return new AccountStore
                {
                    accounts = token.ToObject<List<StoredAccount>>() ?? new List<StoredAccount>()
                };
            }

            return token.ToObject<AccountStore>() ?? new AccountStore();
        }
        catch
        {
            return new AccountStore();
        }
    }

    private static void SaveAccountStore(AccountStore store)
    {
        AtomicWrite(accountsPath, JsonConvert.SerializeObject(store.accounts, Formatting.Indented));
    }

    private static void WriteStatus(bool force)
    {
        if (!force && Time.realtimeSinceStartup < nextStatusAt)
        {
            return;
        }
        nextStatusAt = Time.realtimeSinceStartup + 1f;
        var manager = FindManagerForScene(SceneManager.GetActiveScene().name);
        var sequence = CurrentInnerSequence(manager);
        var payload = new AccountPrepStatus
        {
            requestId = request?.id,
            stage = stage,
            active = active,
            nickname = request?.nickname,
            scene = SceneManager.GetActiveScene().name,
            sequence = sequence?.GetType().FullName,
            accountCaptured = accountCaptured,
            initialMjchip = initialMjchip,
            currentMjchip = currentMjchip,
            completedStories = CompletedStories.OrderBy(id => id).ToList(),
            maxStories = request?.maxStories,
            exhaustedChapters = ExhaustedChapters.OrderBy(id => id).ToList(),
            currentChapterId = currentChapterId == 0 ? null : currentChapterId,
            inaccessibleChapters = InaccessibleChapters.Distinct().OrderBy(id => id).ToList(),
            error = lastError,
            screenshotPath = lastScreenshotPath,
            updatedAt = DateTimeOffset.UtcNow.ToString("O")
        };
        AtomicWrite(statusPath, JsonConvert.SerializeObject(payload, Formatting.Indented));
    }

    private static void SetStage(string value)
    {
        if (stage == value)
        {
            return;
        }
        stage = value;
        ProbeLog.Write("account_prep_stage", new
        {
            stage,
            scene = SceneManager.GetActiveScene().name,
            sequence = CurrentInnerSequence(FindManagerForScene(SceneManager.GetActiveScene().name))?.GetType().FullName,
            currentChapterId = currentChapterId == 0 ? (int?)null : currentChapterId,
            lastStoryId = lastStoryId == 0 ? (int?)null : lastStoryId
        });
        WriteStatus(force: true);
    }

    private static string NormalizeNickname(string? value, string requestId)
    {
        var text = string.IsNullOrWhiteSpace(value)
            ? "JQ" + requestId.Replace("-", "").Substring(0, Math.Min(8, requestId.Replace("-", "").Length))
            : value!.Trim();
        if (text.Length > 14)
        {
            text = text.Substring(0, 14);
        }
        return text;
    }

    private static int? ReadMjchip()
    {
        return UserDataManager.GetData<PlayerProfileData>(UserDataType.PlayerProfile, out var profile)
            ? profile.MJChip
            : null;
    }

    private static object? FindManagerForScene(string scene)
    {
        return scene switch
        {
            "Login" => FindManager("LoginSequenceManager"),
            "Regist" => FindManager("RegistSequenceManager"),
            "Menu" => FindManager("MenuSequenceManager"),
            "YakuhimeStory" => FindManager("MJM.Yakuhime.YakuhimeStorySequenceManager"),
            _ => null
        };
    }

    private static object? FindManager(string fullName)
    {
        return UnityEngine.Object.FindObjectsOfType<MonoBehaviour>()
            .FirstOrDefault(item => item.GetType().FullName == fullName);
    }

    private static object? CurrentInnerSequence(object? manager)
    {
        if (manager == null)
        {
            return null;
        }
        object? sequence = AccessTools.Field(manager.GetType(), "_currentSequence")?.GetValue(manager);
        while (sequence != null)
        {
            var inner = FieldInHierarchy(sequence.GetType(), "sequence")?.GetValue(sequence);
            if (inner == null || ReferenceEquals(inner, sequence))
            {
                return sequence;
            }
            sequence = inner;
        }
        return null;
    }

    private static object CreateYakuhimeQuestList(object parent, bool playEnterVoice, int chapterIndex)
    {
        var type = RequiredType("MJM.Yakuhime.YakuhimeQuestListMenu");
        var create = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Create" && method.GetParameters().Length == 3);
        var enumType = create.GetParameters()[2].ParameterType;
        var han = Enum.ToObject(enumType, chapterIndex);
        return create.Invoke(null, new[] { parent, (object)playEnterVoice, han })
            ?? throw new InvalidOperationException("yakuhime_quest_list_create_failed");
    }

    private static object CreateSequence(string typeName, params object[] args)
    {
        var type = RequiredType(typeName);
        var method = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Where(candidate => candidate.Name == "Create")
            .FirstOrDefault(candidate => ParametersMatch(candidate.GetParameters(), args))
            ?? throw new MissingMethodException(typeName, "Create");
        return method.Invoke(null, args)
            ?? throw new InvalidOperationException(typeName + ".Create returned null");
    }

    private static object CreateInstance(string typeName, params object[] args)
    {
        return Activator.CreateInstance(
            RequiredType(typeName),
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
            binder: null,
            args,
            culture: null
        ) ?? throw new InvalidOperationException(typeName + " constructor returned null");
    }

    private static bool ForceNext(object manager, object next)
    {
        var method = manager.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .FirstOrDefault(candidate => candidate.Name == "RequestForceNextSequence" && candidate.GetParameters().Length == 1)
            ?? throw new MissingMethodException(manager.GetType().FullName, "RequestForceNextSequence");
        return method.Invoke(manager, new[] { next }) as bool? ?? false;
    }

    private static object? InvokeStatic(string typeName, string methodName, params object[] args)
    {
        var type = RequiredType(typeName);
        var method = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Where(candidate => candidate.Name == methodName)
            .FirstOrDefault(candidate => ParametersMatch(candidate.GetParameters(), args))
            ?? throw new MissingMethodException(typeName, methodName);
        return method.Invoke(null, args);
    }

    private static object? Invoke(object instance, string methodName, params object[] args)
    {
        var method = instance.GetType().GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Where(candidate => candidate.Name == methodName)
            .FirstOrDefault(candidate => ParametersMatch(candidate.GetParameters(), args))
            ?? throw new MissingMethodException(instance.GetType().FullName, methodName);
        return method.Invoke(instance, args);
    }

    private static bool ParametersMatch(ParameterInfo[] parameters, object[] args)
    {
        if (parameters.Length != args.Length)
        {
            return false;
        }
        for (var index = 0; index < parameters.Length; index++)
        {
            if (args[index] != null && !parameters[index].ParameterType.IsInstanceOfType(args[index]))
            {
                return false;
            }
        }
        return true;
    }

    private static int InvokeInt(object instance, string methodName)
    {
        return Convert.ToInt32(Invoke(instance, methodName));
    }

    private static bool InvokeBool(object instance, string methodName)
    {
        return Convert.ToBoolean(Invoke(instance, methodName));
    }

    private static object RequiredField(object instance, string name)
    {
        return FieldValue(instance, name)
            ?? throw new MissingFieldException(instance.GetType().FullName, name);
    }

    private static object? FieldValue(object instance, string name)
    {
        return FieldInHierarchy(instance.GetType(), name)?.GetValue(instance);
    }

    private static string? FieldText(object instance, string name)
    {
        return FieldValue(instance, name)?.ToString();
    }

    private static void SetField(object instance, string name, object? value)
    {
        var field = FieldInHierarchy(instance.GetType(), name)
            ?? throw new MissingFieldException(instance.GetType().FullName, name);
        field.SetValue(instance, value);
    }

    private static FieldInfo? FieldInHierarchy(Type type, string name)
    {
        for (var current = type; current != null; current = current.BaseType)
        {
            var field = current.GetField(name, BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
            if (field != null)
            {
                return field;
            }
        }
        return null;
    }

    private static object? GetMember(object instance, string name)
    {
        var property = AccessTools.Property(instance.GetType(), name);
        if (property != null)
        {
            return property.GetValue(instance);
        }
        return FieldValue(instance, name);
    }

    private static void SetMember(object instance, string name, object value)
    {
        var property = AccessTools.Property(instance.GetType(), name);
        if (property != null)
        {
            property.SetValue(instance, value);
            return;
        }
        SetField(instance, name, value);
    }

    private static void SetEnumMember(object instance, string name, string enumName)
    {
        var property = AccessTools.Property(instance.GetType(), name);
        if (property != null)
        {
            property.SetValue(instance, Enum.Parse(property.PropertyType, enumName));
            return;
        }
        var field = FieldInHierarchy(instance.GetType(), name)
            ?? throw new MissingFieldException(instance.GetType().FullName, name);
        field.SetValue(instance, Enum.Parse(field.FieldType, enumName));
    }

    private static object? LoadAccountData()
    {
        var reader = CreateInstance("Regist.AccountFileReader");
        Invoke(reader, "Load");
        return AccessTools.Property(reader.GetType(), "Data")?.GetValue(reader);
    }

    private static Type RequiredType(string name)
    {
        return AccessTools.TypeByName(name) ?? throw new TypeLoadException(name);
    }

    private static void SetStaticProperty(string typeName, string propertyName, object value)
    {
        var property = AccessTools.Property(RequiredType(typeName), propertyName)
            ?? throw new MissingMemberException(typeName, propertyName);
        property.SetValue(null, value);
    }

    private static string? CurrentGameState(GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonus"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallenge"),
            _ => FieldText(manager, "mGameStateNormal")
        };
    }

    private static string? CurrentGameRequestState(GameManager manager)
    {
        var mode = FieldText(manager, "mGameMode");
        return mode switch
        {
            "YakumanBonus" => FieldText(manager, "mGameStateBonusRequest"),
            "ParenChallenge" => FieldText(manager, "mGameStateChallengeRequest"),
            _ => FieldText(manager, "mGameStateNormalRequest")
        };
    }

    private static string SafeName(string? value)
    {
        return string.IsNullOrWhiteSpace(value)
            ? "unknown"
            : value!.Replace('`', '_').Replace('+', '_').Replace('.', '_');
    }

    private static void CaptureDiagnosticScreenshot(string reason)
    {
        if (string.IsNullOrEmpty(rootPath) || Time.realtimeSinceStartup < nextScreenshotAt)
        {
            return;
        }
        nextScreenshotAt = Time.realtimeSinceStartup + 15f;
        CaptureScreenshot(reason, updateLatest: false);
    }

    private static void CaptureProgressScreenshot(string scene)
    {
        if (!active || string.IsNullOrEmpty(rootPath) || Time.realtimeSinceStartup < nextProgressScreenshotAt)
        {
            return;
        }
        nextProgressScreenshotAt = Time.realtimeSinceStartup + 20f;
        CaptureScreenshot("progress_" + SafeName(stage) + "_" + SafeName(scene), updateLatest: true);
    }

    private static void CaptureScreenshot(string reason, bool updateLatest)
    {
        try
        {
            var directory = Path.Combine(rootPath, "screenshots");
            Directory.CreateDirectory(directory);
            var path = Path.Combine(
                directory,
                $"{DateTimeOffset.UtcNow:yyyyMMdd_HHmmss}_{reason}_{SceneManager.GetActiveScene().name}.png"
            );
            ScreenCapture.CaptureScreenshot(path);
            lastScreenshotPath = path;
            if (updateLatest)
            {
                lastScreenshotPath = Path.Combine(directory, "latest.png");
                UnityThreadCopyAfterDelay(path, lastScreenshotPath);
            }
            ProbeLog.Write("account_prep_screenshot_requested", new { reason, path = lastScreenshotPath });
        }
        catch (Exception ex)
        {
            ProbeLog.Write("account_prep_screenshot_failed", new { reason, error = ex.Message });
        }
    }

    private static void UnityThreadCopyAfterDelay(string source, string destination)
    {
        var runner = UnityEngine.Object.FindObjectOfType<AccountPrepRunner>();
        if (runner == null)
        {
            return;
        }
        runner.StartCoroutine(CopyScreenshotWhenReady(source, destination));
    }

    private static IEnumerator CopyScreenshotWhenReady(string source, string destination)
    {
        var deadline = Time.realtimeSinceStartup + 5f;
        while (Time.realtimeSinceStartup < deadline && !File.Exists(source))
        {
            yield return null;
        }
        if (!File.Exists(source))
        {
            yield break;
        }
        try
        {
            File.Copy(source, destination, overwrite: true);
        }
        catch (Exception ex)
        {
            ProbeLog.Write("account_prep_screenshot_latest_failed", new { source, destination, error = ex.Message });
        }
    }

    private static void AtomicWrite(string path, string content)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temp = path + ".tmp";
        for (var attempt = 0; attempt < 12; attempt += 1)
        {
            try
            {
                File.WriteAllText(temp, content);
                if (File.Exists(path))
                {
                    File.Replace(temp, path, null);
                }
                else
                {
                    File.Move(temp, path);
                }
                return;
            }
            catch (IOException) when (attempt < 11)
            {
                System.Threading.Thread.Sleep(20);
            }
            catch (UnauthorizedAccessException) when (attempt < 11)
            {
                System.Threading.Thread.Sleep(20);
            }
            catch (Exception ex)
            {
                try
                {
                    File.Delete(temp);
                }
                catch
                {
                    // A later status update will reuse or replace this temp file.
                }
                ProbeLog.Write("account_prep_atomic_write_skipped", new { path, error = ex.Message });
                return;
            }
        }
    }

    private static void ArchiveRequest(string suffix)
    {
        try
        {
            if (!File.Exists(requestPath))
            {
                return;
            }
            var destination = Path.Combine(
                rootPath,
                $"request.{request?.id}.{suffix}.{DateTimeOffset.UtcNow:yyyyMMdd_HHmmss}.json"
            );
            File.Move(requestPath, destination);
        }
        catch (Exception ex)
        {
            ProbeLog.Write("account_prep_request_archive_failed", new { suffix, error = ex.Message });
        }
    }
}

[HarmonyPatch]
internal static class AccountPrepOpenBrowserPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("MJM.Network.NetworkUtil")
            ?? throw new TypeLoadException("MJM.Network.NetworkUtil");
        return AccessTools.Method(type, "OpenBrowser")
            ?? throw new MissingMethodException(type.FullName, "OpenBrowser");
    }

    private static bool Prefix(string url)
    {
        if (!AccountPrepBridge.ShouldBlockBrowser(url))
        {
            return true;
        }
        ProbeLog.Write("account_prep_browser_suppressed", new { reason = "account_registration_referral" });
        return false;
    }
}

[HarmonyPatch]
internal static class AccountPrepExceptionPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("MJM.ExceptionExportMonoBehaviourBase")
            ?? throw new TypeLoadException("MJM.ExceptionExportMonoBehaviourBase");
        return AccessTools.Method(type, "CatchException", new[] { typeof(string), typeof(Exception) })
            ?? throw new MissingMethodException(type.FullName, "CatchException");
    }

    private static void Prefix(string sequence, Exception e)
    {
        if (AccountPrepBridge.IsActive)
        {
            ProbeLog.Write("account_prep_client_exception", new
            {
                sequence,
                error = e.ToString()
            });
        }
    }
}

[HarmonyPatch]
internal static class AccountPrepSystemFadePatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("MJM.Dialogue.SystemFade")
            ?? throw new TypeLoadException("MJM.Dialogue.SystemFade");
        return AccessTools.Method(type, "Start")
            ?? throw new MissingMethodException(type.FullName, "Start");
    }

    private static bool Prefix(System.Action endCallback)
    {
        if (!AccountPrepBridge.IsActive)
        {
            return true;
        }
        endCallback?.Invoke();
        ProbeLog.Write("account_prep_story_fade_skipped", new { reason = "background_story_playback" });
        return false;
    }
}

[HarmonyPatch]
internal static class AccountPrepYakuhimeUnlockPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("MJM.Yakuhime.YakuhimeUnlockAnim")
            ?? throw new TypeLoadException("MJM.Yakuhime.YakuhimeUnlockAnim");
        return AccessTools.Method(type, "RequestUnlockAnim")
            ?? throw new MissingMethodException(type.FullName, "RequestUnlockAnim");
    }

    private static bool Prefix(ref IEnumerator __result, System.Action endAction)
    {
        if (!AccountPrepBridge.IsActive)
        {
            return true;
        }
        __result = FinishWithoutVisualEffect(endAction);
        ProbeLog.Write("account_prep_yakuhime_unlock_effect_skipped", new { reason = "background_story_playback" });
        return false;
    }

    private static IEnumerator FinishWithoutVisualEffect(System.Action endAction)
    {
        yield return null;
        endAction?.Invoke();
    }
}
