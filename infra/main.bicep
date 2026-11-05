targetScope = 'subscription'
param environmentName string
param location string = 'westeurope'
param resourceGroupName string = 'rg-${environmentName}'
param operatorPrincipalId string
var tags = {
  'azd-env-name': environmentName
  'lab-id': '2026-11-05-foundry-django-upgrade-evidence'
  'proposal-hash': '77d253e1f6a9fa26b0b597b999415613111e11a9b21bef70be053d2f38688542'
  'cleanup-at': '2026-11-09T10:00:00+01:00'
}
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}
module core './core.bicep' = {
  name: 'django-core'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: tags
    operatorPrincipalId: operatorPrincipalId
  }
}
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_AI_ACCOUNT_NAME string = core.outputs.accountName
output AZURE_AI_PROJECT_ENDPOINT string = core.outputs.projectEndpoint
output AZURE_AI_PROJECT_ID string = core.outputs.projectId
output AZURE_AI_ENDPOINT string = core.outputs.endpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = 'django-luna'
output AZURE_DECISION_DEPLOYMENT string = 'django-decision'
output BING_CONNECTION_ID string = core.outputs.bingConnectionId
output BING_CUSTOM_SEARCH_INSTANCE_NAME string = 'django-official'
output BING_RESOURCE_ID string = core.outputs.bingId
