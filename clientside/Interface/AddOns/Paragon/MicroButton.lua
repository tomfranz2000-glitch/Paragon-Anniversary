--[[
	Runtime port of the upstream Paragon-Anniversary edits to
	Interface\FrameXML\MainMenuBarMicroButtons.{lua,xml}.

	Overriding FrameXML files from a patch MPQ trips the 3.3.5 client's UI
	signature check ("Your game interface files are corrupt"), so the same
	button is created here at addon load time instead. The handler functions
	below are copied verbatim from the upstream file.
]]

-- ============================================================
-- Upstream functions (verbatim from MainMenuBarMicroButtons.lua)
-- ============================================================

function ParagonMicroButton_SetPushed()
	ParagonMicroButton:SetButtonState("PUSHED", 1);
end

function ParagonMicroButton_SetNormal()
	ParagonMicroButton:SetButtonState("NORMAL");
end

function ToggleParagonFrame()
	if (UIParagon:IsShown()) then
		UIParagon:Hide();
		ParagonMicroButton_SetNormal();
		if ParagonMicroButton and ParagonMicroButton.Notification then
			ParagonMicroButton.Notification.dismissed = false
		end
		if ParagonMicroButton_UpdateNotification then
			ParagonMicroButton_UpdateNotification()
		end
	else
		UIParagon:Show();
		ParagonMicroButton_SetPushed();
		if ParagonMicroButton.Notification then
			ParagonMicroButton.Notification:Hide()
		end
	end
end

function ParagonMicroButton_Notification_OnLoad(self)
	self.pulseTimer = 0
	self.dismissed = false  -- Track if user manually dismissed the notification
end

function ParagonMicroButton_Notification_OnUpdate(self, elapsed)
	if self:IsShown() then
		self.pulseTimer = self.pulseTimer + elapsed
		local alpha = 0.5 + (math.sin(self.pulseTimer * 3) * 0.3)  -- Pulse between 0.2 and 0.8
		if self.Glow then
			self.Glow:SetAlpha(alpha)
		end
	end
end

function ParagonMicroButton_Notification_OnClick(self)
	self.dismissed = true
	self:Hide()
	PlaySound("igMainMenuOptionCheckBoxOn")
end

function ParagonMicroButton_Notification_OnEnter(self)
	local Locales = GetLocaleTable and GetLocaleTable() or {}
	local title = Locales.NOTIFICATION_TITLE or "Unspent Paragon Points"
	local message = Locales.NOTIFICATION_MESSAGE or "You have unspent Paragon points!"
	local dismiss = Locales.NOTIFICATION_DISMISS or "Click to dismiss this notification."

	GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
	GameTooltip:SetText(title, 1.0, 0.82, 0, 1)
	GameTooltip:AddLine(message, 1, 1, 1, 1)
	GameTooltip:AddLine(" ", 1, 1, 1, 1)
	GameTooltip:AddLine(dismiss, 0.5, 0.5, 0.5, 1)
	GameTooltip:Show()
end

-- Update notification visibility based on available points
function ParagonMicroButton_UpdateNotification()
	if not ParagonMicroButton or not ParagonMicroButton.Notification then
		return
	end

	local notification = ParagonMicroButton.Notification

	-- Only show if:
	-- 1. Player has unspent points
	-- 2. Notification was not manually dismissed
	-- 3. Paragon frame is not currently open
	local hasUnspentPoints = ParagonData and ParagonData.availablePoints and ParagonData.availablePoints > 0
	local frameNotOpen = not (UIParagon and UIParagon:IsShown())

	if hasUnspentPoints and not notification.dismissed and frameNotOpen then
		notification:Show()
	else
		notification:Hide()
	end
end

-- Re-enable notification when player gains a level
function ParagonMicroButton_OnLevelUp()
	if ParagonMicroButton and ParagonMicroButton.Notification then
		ParagonMicroButton.Notification.dismissed = false
		ParagonMicroButton_UpdateNotification()
	end
end

-- ============================================================
-- Button construction (port of the upstream XML additions)
-- ============================================================

local btn = CreateFrame("Button", "ParagonMicroButton", MainMenuBarArtFrame, "MainMenuBarMicroButton")
btn:SetSize(26, 58)
LoadMicroButtonTextures(btn, "Abilities")
btn:SetScript("OnClick", ToggleParagonFrame)
btn:SetScript("OnEnter", function(self)
	self.tooltipText = MicroButtonTooltipText("Paragon Anniversary", "NIL");
	GameTooltip_AddNewbieTip(self, self.tooltipText, 1.0, 1.0, 1.0, "View and manage your Paragon Anniversary statistics and earn rewards.");
end)
btn:SetScript("OnLeave", function() GameTooltip:Hide() end)

btn.texture = btn:CreateTexture("ParagonMicroButtonTexture", "OVERLAY")
btn.texture:SetSize(20, 20)
btn.texture:SetPoint("TOP", 0, -25)

local note = CreateFrame("Button", "ParagonMicroButtonNotification", btn)
note:SetFrameLevel(10)
note:SetSize(18, 18)
note:SetPoint("TOP", btn, "TOP", 8, -20)
note:Hide()
btn.Notification = note

note.Bg = note:CreateTexture("ParagonMicroButtonNotificationBg", "BACKGROUND")
note.Bg:SetTexture("Interface/Buttons/UI-GroupLoot-Coin-Up")
note.Bg:SetSize(18, 18)
note.Bg:SetPoint("CENTER")

note.Glow = note:CreateTexture("ParagonMicroButtonNotificationGlow", "OVERLAY")
note.Glow:SetTexture("Interface/Buttons/UI-GroupLoot-Coin-Up")
note.Glow:SetBlendMode("ADD")
note.Glow:SetAlpha(0.8)
note.Glow:SetSize(22, 22)
note.Glow:SetPoint("CENTER")

ParagonMicroButton_Notification_OnLoad(note)
note:SetScript("OnUpdate", ParagonMicroButton_Notification_OnUpdate)
note:SetScript("OnClick", ParagonMicroButton_Notification_OnClick)
note:SetScript("OnEnter", ParagonMicroButton_Notification_OnEnter)
note:SetScript("OnLeave", function() GameTooltip:Hide() end)

-- ============================================================
-- Micro bar layout: upstream narrows every button 28 -> 26 and
-- tightens spacing -3 -> -2 to make room, starting at x=550.
-- Reapplied inside the UpdateMicroButtons hook because stock code
-- re-anchors the bar (e.g. entering/leaving vehicles).
--
-- Local change (user spec 2026-08-18): the Help micro button (GM
-- ticket "?") is sacrificed so BOTH custom buttons fit at full
-- width — this layout is the bar's single authority (it hooks
-- UpdateMicroButtons last), and ezCollections' CollectionsMicroButton
-- takes the freed slot in its default position after LFG. Without
-- this, the two addons fight over the same chain and whichever
-- hook runs last buries the other's button (ours ran last: the
-- collections button vanished under MainMenu). Help stays
-- reachable via /run ToggleHelpFrame() if it is ever missed.
-- ============================================================

local function BuildMicroButtons()
	local buttons = {
		CharacterMicroButton, btn, SpellbookMicroButton, TalentMicroButton,
		AchievementMicroButton, QuestLogMicroButton, SocialsMicroButton,
		PVPMicroButton, LFDMicroButton,
	}
	if CollectionsMicroButton then
		buttons[#buttons + 1] = CollectionsMicroButton
	end
	buttons[#buttons + 1] = MainMenuMicroButton
	return buttons
end

local function LayoutMicroButtons()
	-- re-hidden every pass: ezCollections' own layout and the vehicle
	-- transitions Show() every stock button again
	HelpMicroButton:Hide()
	local buttons = BuildMicroButtons()
	for _, b in ipairs(buttons) do
		b:SetWidth(26)
	end
	CharacterMicroButton:ClearAllPoints()
	CharacterMicroButton:SetPoint("BOTTOMLEFT", MainMenuBarArtFrame, "BOTTOMLEFT", 550, 2)
	for i = 2, #buttons do
		buttons[i]:ClearAllPoints()
		buttons[i]:SetPoint("BOTTOMLEFT", buttons[i - 1], "BOTTOMRIGHT", -2, 0)
	end
end

LayoutMicroButtons()

-- Upstream's addition to UpdateMicroButtons(), as a post-hook
hooksecurefunc("UpdateMicroButtons", function()
	LayoutMicroButtons()
	if ( UIParagon and UIParagon:IsShown() ) then
		ParagonMicroButton:SetButtonState("PUSHED", 1);
	else
		ParagonMicroButton:Enable();
		ParagonMicroButton:SetButtonState("NORMAL");
	end
end)
